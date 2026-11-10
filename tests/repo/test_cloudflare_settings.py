import copy
import io
import re
import sqlite3
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools.repo import cloudflare_settings as cs

ZONE = "/zones/z1"
ENTRYPOINT = f"{ZONE}/rulesets/phases/{cs.REDIRECT_PHASE}/entrypoint"
RATE_LIMITS = f"{ZONE}/rulesets/phases/{cs.RATE_LIMIT_PHASE}/entrypoint"
BUCKETS = "/accounts/a1/r2/buckets"
DATABASES = "/accounts/a1/d1/database"
STATS = f"{BUCKETS}/{cs.STATS_BUCKET}"
SECRETS = f"/accounts/a1/workers/scripts/{cs.API_WORKER}/secrets"
QUERY = f"{DATABASES}/u1/query"
ADDRESSES = "/accounts/a1/email/routing/addresses"
DESTINATION = "owner@example.com"
FORWARD = "hello, Press"
# What Email Routing says it needs, as it lists them: a TXT record whole, a host with its final dot.
ROUTING_DNS = [{"type": "MX", "name": cs.ZONE, "content": "route1.mx.cloudflare.net.", "priority": 50},
               {"type": "TXT", "name": f"cf2024-1._domainkey.{cs.ZONE}", "content": '"v=DKIM1; p=AAAABBBB"'}]
# What Email Service says a domain onboarded for sending needs, made up in its shape.
SENDING = f"{ZONE}/email/sending/subdomains"
SENDING_DNS = [{"type": "MX", "name": f"cf-bounce.{cs.ZONE}", "content": "route1.mx.cloudflare.net", "priority": 10,
                "ttl": 1},
               {"type": "TXT", "name": f"cf-bounce.{cs.ZONE}", "content": "v=spf1 include:_spf.mx.cloudflare.net ~all",
                "ttl": 1},
               {"type": "TXT", "name": f"cf-bounce._domainkey.{cs.ZONE}", "content": "v=DKIM1; p=CCCCDDDD", "ttl": 1},
               {"type": "TXT", "name": f"_dmarc.{cs.ZONE}", "content": "v=DMARC1; p=reject;", "ttl": 1}]


class FakeCloudflare:
    """Cloudflare's API for one zone, as tools/repo/cloudflare_settings.py sees it."""

    def __init__(self):
        self.settings = {"always_use_https": "off", "min_tls_version": "1.0", "email_obfuscation": "off"}
        self.dnssec = "disabled"
        self.rulesets = {}  # entry point path: its rules, as Cloudflare keeps them
        self.buckets = set()
        self.lifecycle = None
        self.tokens = None   # the account's API tokens; None: this token may not read them, as CI's
        self.other_zones = []   # zones besides this one the token can see
        self.accounts = 1   # how many accounts the token can see
        # The Workers the token can see: Cloudflare lists those alone. The apply token sees them all.
        self.workers = ["firmware-liberator", "firmware-liberator-api", "another-site"]
        self.readable = set()   # of CI_REFUSED's paths, those the token may read
        self.routing = False
        self.email_rules = []
        self.catch_all = {"id": "c1", **copy.deepcopy(cs.EMAIL_CATCH_ALL)}
        self.addresses = []
        # The Workers' custom domains, as wrangler makes them; DNS records listed as Cloudflare does,
        # a long TXT record in quoted strings, a host without its final dot.
        self.dns = [{"id": "w1", "type": "AAAA", "name": cs.ZONE, "content": "100::",
                     "meta": {"origin_worker_id": "x"}}]
        self.databases = []   # the account's D1 databases
        self.migrations = None   # what d1_migrations records; None: no such table, never migrated
        self.bound = "u1"   # the database id website/api/wrangler.jsonc binds
        self.stats_domains = []   # the stats bucket's custom domains
        self.r2_dev = False   # whether its r2.dev address is on
        self.secrets = list(cs.API_SECRETS)   # the API Worker's secrets, by name; None: no such Worker
        self.sending = []   # the zone's domains onboarded for Email Service's sending
        self.sent = []
        self.ids = iter(range(100, 1000))

    @property
    def redirects(self):
        return self.rulesets.get(ENTRYPOINT)

    def __call__(self, path, method="GET", body=None, ok_missing=False, ok_forbidden=False, whole=False):
        if path == QUERY and body["sql"] == "SELECT name FROM d1_migrations ORDER BY id":   # a read
            if self.migrations is None:
                raise cs.SettingsError(f"POST {QUERY}: HTTP 400: no such table: d1_migrations: SQLITE_ERROR")
            return [{"results": [{"name": name} for name in self.migrations], "success": True}]
        if method != "GET":
            self.sent.append((method, path, body))
            if path == f"{ZONE}/dns_records":
                # Cloudflare writes a CAA record's content from its data, and lists a TXT record's quoted.
                if "data" in body:
                    content = f'{body["data"]["flags"]} {body["data"]["tag"]} "{body["data"]["value"]}"'
                else:
                    content = f'"{body["content"]}"' if body["type"] == "TXT" else body["content"]
                self.dns.append({"id": f"d{next(self.ids)}", "meta": {}, **body, "content": content})
            elif path == f"{ZONE}/dnssec":
                self.dnssec = "active"   # as once the registrar's DS record is seen
            elif path.startswith(f"{ZONE}/dns_records/"):
                self.dns = [r for r in self.dns if r["id"] != path.rsplit("/", 1)[1]]
            elif path == f"{ZONE}/email/routing/enable":
                self.routing = True
            elif path == f"{ZONE}/email/routing/rules/catch_all":
                self.catch_all = {"id": "c1", **copy.deepcopy(body)}
            elif path == f"{ZONE}/email/routing/rules":
                self.email_rules.append({"id": f"e{next(self.ids)}", **copy.deepcopy(body)})
            elif path.startswith(f"{ZONE}/email/routing/rules/"):
                self.email_rules = [r for r in self.email_rules if r["id"] != path.rsplit("/", 1)[1]]
            elif path == ADDRESSES:
                self.addresses.append({"email": body["email"], "verified": None})
            elif path == BUCKETS:
                self.buckets.add(body["name"])
            elif path == f"{STATS}/domains/custom":
                self.stats_domains.append({"domain": body["domain"], "enabled": body["enabled"]})
            elif path == f"{STATS}/domains/managed":
                self.r2_dev = body["enabled"]
            elif path == DATABASES:
                self.databases.append({"name": body["name"], "uuid": "u1"})
            elif path == SENDING:
                self.sending.append({"tag": "s1", "name": body["name"], "enabled": True})
                return copy.deepcopy(self.sending[-1])
            elif path == SECRETS and method == "PUT":
                self.secrets = [name for name in self.secrets or [] if name != body["name"]] + [body["name"]]
            elif path == QUERY:
                self.migrations = (self.migrations or []) + re.findall(r"VALUES \('([^']+)'\);\s*$", body["sql"])
            elif path.endswith("/lifecycle"):
                self.lifecycle = copy.deepcopy(body)
            elif "/rulesets/phases/" in path:
                # Cloudflare gives each rule its id and version, and keeps the rest as sent.
                self.rulesets[path] = [{"id": "r1", "version": "1", **copy.deepcopy(rule)} for rule in body["rules"]]
            else:
                self.settings[path.rsplit("/", 1)[1]] = body["value"]
            return None
        if path == f"/zones?name={cs.ZONE}":
            return [{"id": "z1", "name": cs.ZONE, "account": {"id": "a1"}}]
        if path == "/zones?per_page=50":
            return [{"id": "z1", "name": cs.ZONE}] + [{"id": f"o{n}", "name": name}
                                                      for n, name in enumerate(self.other_zones)]
        if path == "/accounts?per_page=50":
            return [{"id": f"a{n + 1}"} for n in range(self.accounts)]
        if path == "/accounts/a1/tokens":
            assert ok_forbidden
            return copy.deepcopy(self.tokens)
        if path in {template.format(account="a1", zone="z1") for _, template in cs.CI_REFUSED + cs.DEPLOY_REFUSED}:
            assert ok_forbidden
            return {} if path in self.readable else None
        if path == f"{STATS}/domains/custom":
            return {"domains": copy.deepcopy(self.stats_domains)}
        if path == f"{STATS}/domains/managed":
            return {"enabled": self.r2_dev}
        if path == SECRETS:
            assert ok_missing
            return None if self.secrets is None else [{"name": name, "type": "secret_text"} for name in self.secrets]
        if path == f"{DATABASES}?name={cs.D1_DATABASE}":
            return copy.deepcopy(self.databases)
        if path == "/accounts/a1/workers/scripts":
            return [{"id": name, "tag": f"t{n + 1}"} for n, name in enumerate(self.workers)]
        if path == f"{ZONE}/dns_records?per_page=500":
            return {"result": copy.deepcopy(self.dns), "result_info": {"total_count": len(self.dns)}}
        if path == f"{ZONE}/email/routing/dns":
            return copy.deepcopy(ROUTING_DNS)
        if path == SENDING:
            return copy.deepcopy(self.sending)
        if path.startswith(f"{SENDING}/") and path.endswith("/dns"):
            return copy.deepcopy(SENDING_DNS)
        if path == f"{ZONE}/email/routing":
            return {"enabled": self.routing}
        if path == f"{ZONE}/email/routing/rules?per_page=50":
            return copy.deepcopy(self.email_rules + [self.catch_all])
        if path == f"{ZONE}/email/routing/rules/catch_all":
            return copy.deepcopy(self.catch_all)
        if path == f"{ADDRESSES}?per_page=50":
            return copy.deepcopy(self.addresses)
        if path.startswith(f"{BUCKETS}/") and path.endswith("/lifecycle"):
            assert ok_missing
            return self.lifecycle
        if path.startswith(f"{BUCKETS}/"):
            assert ok_missing
            name = path.rsplit("/", 1)[1]
            return {"name": name} if name in self.buckets else None
        if path == f"{ZONE}/dnssec":
            return {"status": self.dnssec}
        if path.startswith(f"{ZONE}/settings/"):
            return {"id": path.rsplit("/", 1)[1], "value": self.settings[path.rsplit("/", 1)[1]]}
        if "/rulesets/phases/" in path:
            assert ok_missing
            return {"rules": self.rulesets[path]} if path in self.rulesets else None
        raise AssertionError(f"unexpected {method} {path}")


def run(fake, *args):
    out = io.StringIO()
    with mock.patch.object(cs, "api", fake), redirect_stdout(out), \
            mock.patch.object(cs, "bound_database_id", lambda: fake.bound), \
            mock.patch.dict("os.environ", {cs.EMAIL_DESTINATION: DESTINATION, cs.EMAIL_FORWARD: FORWARD}):
        code = cs.main(list(args))
    return code, out.getvalue()


class CloudflareSettingsTests(unittest.TestCase):
    def test_check_only_says_what_differs(self):
        fake = FakeCloudflare()
        code, out = run(fake)
        self.assertEqual((code, fake.sent), (1, []))
        self.assertIn("always_use_https: is off, should be on", out)
        self.assertIn("redirects: are [], should be ['www-to-apex']", out)
        self.assertIn(f"{cs.READOUTS_BUCKET}: does not exist", out)
        self.assertIn("rate limits: are [], should be ['api-flood']", out)
        self.assertIn("Email Routing: is off", out)
        self.assertIn(f"TXT _dmarc.{cs.ZONE} 'v=DMARC1; ", out)

    def test_apply_sets_them_and_then_sends_nothing(self):
        fake = FakeCloudflare()
        self.assertEqual(run(fake, "apply")[0], 0)
        self.assertEqual(fake.settings, cs.SETTINGS)
        self.assertEqual([cs.comparable(rule) for rule in fake.redirects], cs.REDIRECTS)
        self.assertEqual(fake.buckets, {cs.READOUTS_BUCKET, cs.STATS_BUCKET})
        self.assertEqual(fake.stats_domains, [{"domain": cs.STATS_HOST, "enabled": True}])
        for phase, (_, rules) in cs.RULESETS.items():
            entry = f"{ZONE}/rulesets/phases/{phase}/entrypoint"
            self.assertEqual([cs.comparable(rule, rules[0]) for rule in fake.rulesets[entry]], rules, phase)
        self.assertEqual([cs.comparable(rule, cs.RATE_LIMITS[0]) for rule in fake.rulesets[RATE_LIMITS]],
                         cs.RATE_LIMITS)
        self.assertIsNone(fake.lifecycle, "a new bucket has no lifecycle rule, and is given none")
        self.assertTrue(fake.routing)
        self.assertEqual(fake.databases, [{"name": cs.D1_DATABASE, "uuid": "u1"}])
        self.assertEqual(fake.migrations, cs.migration_files())
        self.assertEqual(fake.addresses, [{"email": DESTINATION, "verified": None}])
        fake.addresses[0]["verified"] = "2026-11-09T18:39:03Z"   # its owner followed Cloudflare's link
        fake.sent.clear()
        code, out = run(fake, "apply")
        self.assertEqual((code, fake.sent), (0, []))
        self.assertIn("every setting is as tools/repo/cloudflare_settings.py says", out)

    def test_a_changed_redirect_is_put_back(self):
        fake = FakeCloudflare()
        run(fake, "apply")
        fake.redirects[0]["action_parameters"]["from_value"]["status_code"] = 302
        fake.sent.clear()
        run(fake, "apply")
        self.assertEqual(fake.sent, [("PUT", ENTRYPOINT, {"rules": cs.REDIRECTS})])

    def test_www_goes_to_the_domain_wrangler_serves(self):
        config = (Path(__file__).resolve().parents[2] / "website/wrangler.jsonc").read_text(encoding="utf-8")
        for host in (cs.ZONE, f"www.{cs.ZONE}"):
            self.assertIn(f'{{ "pattern": "{host}", "custom_domain": true }}', config)
        self.assertEqual(cs.REDIRECTS[0]["expression"], f'(http.host eq "www.{cs.ZONE}")')

    def test_a_lifecycle_rule_added_by_hand_is_taken_away_as_readouts_are_kept_for_good(self):
        fake = FakeCloudflare()
        fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09"}]
        run(fake, "apply")
        fake.lifecycle = {"rules": [{"id": "readouts-expire", "enabled": True, "conditions": {"prefix": "readouts/"},
                                     "deleteObjectsTransition": {"condition": {"type": "Age", "maxAge": 86400}}}]}
        fake.sent.clear()
        code, out = run(fake)
        self.assertEqual(code, 1)
        self.assertIn("lifecycle: has ['readouts-expire'], should have none: readouts are kept for good", out)
        run(fake, "apply")
        self.assertEqual(fake.sent, [("PUT", f"{BUCKETS}/{cs.READOUTS_BUCKET}/lifecycle", {"rules": []})])
        self.assertEqual(run(fake)[0], 0)

    def test_the_readouts_go_to_the_bucket_it_makes(self):
        config = (Path(__file__).resolve().parents[2] / "website/api/wrangler.jsonc").read_text(encoding="utf-8")
        self.assertIn(f'"bucket_name": "{cs.READOUTS_BUCKET}"', config)

    def test_the_token_comes_from_the_environment_or_env(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            with mock.patch.object(cs, "ENV_FILE", env_file), \
                    mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": ""}):
                with self.assertRaisesRegex(cs.SettingsError, "no CLOUDFLARE_API_TOKEN: in the environment or .env.infra"):
                    cs.token()
                env_file.write_text("CLOUDFLARE_API_TOKEN=from-the-file\n", encoding="utf-8")
                self.assertEqual(cs.token(), "from-the-file")
                with mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": "from-the-environment"}):
                    self.assertEqual(cs.token(), "from-the-environment")
                    # The file is not even read: a line it cannot parse is no matter.
                    env_file.write_text("not a setting\n", encoding="utf-8")
                    self.assertEqual(cs.token(), "from-the-environment")

    def test_a_rate_limit_changed_by_hand_is_put_back(self):
        fake = FakeCloudflare()
        run(fake, "apply")
        fake.rulesets[RATE_LIMITS][0]["ratelimit"]["requests_per_period"] = 1000
        fake.sent.clear()
        run(fake, "apply")
        self.assertEqual(fake.sent, [("PUT", RATE_LIMITS, {"rules": cs.RATE_LIMITS})])

    def test_the_rate_limit_is_on_the_api_workers_host_alone(self):
        config = (Path(__file__).resolve().parents[2] / "website/api/wrangler.jsonc").read_text(encoding="utf-8")
        self.assertIn(f'{{ "pattern": "{cs.API_HOST}", "custom_domain": true }}', config)
        self.assertEqual(cs.RATE_LIMITS[0]["expression"], f'(http.host eq "{cs.API_HOST}")')
        # What the free plan allows: one rule, by data centre and address, over 10 seconds.
        self.assertEqual(len(cs.RATE_LIMITS), 1)
        self.assertEqual(cs.RATE_LIMITS[0]["ratelimit"]["characteristics"], ["cf.colo.id", "ip.src"])
        self.assertEqual((cs.RATE_LIMITS[0]["ratelimit"]["period"], cs.RATE_LIMITS[0]["ratelimit"]["mitigation_timeout"]),
                         (10, 10))

    def test_dns_records_are_those_written_down_email_routings_and_the_workers(self):
        fake = FakeCloudflare()
        fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09T18:39:03Z"}]
        run(fake, "apply")
        sending = [r for r in SENDING_DNS if not r["name"].startswith("_dmarc.")]
        self.assertEqual(sorted((r["type"], r["name"]) for r in fake.dns), sorted(
            [("AAAA", cs.ZONE)] + [(r["type"], r["name"]) for r in cs.DNS_RECORDS + ROUTING_DNS + sending]))
        # Listed as Cloudflare does, they are no difference.
        for record in fake.dns:
            if record["type"] == "TXT":
                record["content"] = '"' + record["content"][1:-1][:5] + '" "' + record["content"][1:-1][5:] + '"'
            elif record["type"] == "MX":
                record["content"] = record["content"].rstrip(".")
        self.assertEqual(run(fake)[0], 0)
        # One added by hand is deleted, one changed put back.
        fake.dns.append({"id": "h1", "type": "A", "name": f"mail.{cs.ZONE}", "content": "192.0.2.50", "meta": {}})
        dmarc = next(r for r in fake.dns if r["name"] == f"_dmarc.{cs.ZONE}")
        dmarc["content"] = '"v=DMARC1; p=none"'
        fake.sent.clear()
        code, out = run(fake)
        self.assertEqual(code, 1)
        self.assertIn(f"A mail.{cs.ZONE} '192.0.2.50': is not written down", out)
        self.assertIn(f"TXT _dmarc.{cs.ZONE} 'v=DMARC1; p=none': is not written down", out)
        code, out = run(fake, "apply")
        self.assertEqual(sorted(method for method, _, _ in fake.sent), ["DELETE", "DELETE", "POST"])
        self.assertIn(f"A mail.{cs.ZONE} '192.0.2.50': removed", out)
        self.assertEqual(run(fake)[0], 0)

    def test_each_address_named_is_forwarded_and_no_address_is_shown(self):
        fake = FakeCloudflare()
        fake.addresses = [{"email": DESTINATION.upper(), "verified": "2026-11-09T18:39:03Z"}]
        code, out = run(fake, "apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted((r["matchers"][0]["value"], r["actions"][0]["value"], r["enabled"])
                                for r in fake.email_rules),
                         [(f"hello@{cs.ZONE}", [DESTINATION], True), (f"press@{cs.ZONE}", [DESTINATION], True)])
        # Listed in another order, they are no difference.
        fake.email_rules.reverse()
        self.assertEqual(run(fake)[0], 0)
        # One sent elsewhere, one more made by hand, and a catch-all turned on are put back.
        fake.email_rules[0]["actions"][0]["value"] = ["someone@example.org"]
        fake.email_rules.append({"id": "e9", "name": "sales", "matchers": [{"type": "literal", "field": "to",
                                 "value": f"sales@{cs.ZONE}"}], "actions": [{"type": "drop"}], "enabled": True})
        fake.catch_all["enabled"] = True
        fake.addresses.append({"email": "someone@example.org", "verified": None})
        fake.sent.clear()
        code, out = run(fake, "apply")
        self.assertEqual(code, 1)   # the other address is left to the dashboard
        self.assertIn(f"email rule 4: is to be made, from {cs.EMAIL_FORWARD}", out)
        self.assertIn("email catch-all: forwards as written nowhere", out)
        self.assertIn(f"destination addresses: 1 besides {cs.EMAIL_DESTINATION}", out)
        for private in ("@example.", "hello", "press", "sales"):
            self.assertNotIn(private, out.lower())
        self.assertEqual(sorted(r["matchers"][0]["value"] for r in fake.email_rules),
                         [f"hello@{cs.ZONE}", f"press@{cs.ZONE}"])
        self.assertEqual(fake.catch_all, {"id": "c1", **cs.EMAIL_CATCH_ALL})

    def test_an_unverified_destination_is_left_to_its_owner(self):
        fake = FakeCloudflare()
        run(fake, "apply")
        fake.sent.clear()
        code, out = run(fake, "apply")
        self.assertEqual((code, fake.sent), (1, []))
        self.assertIn(f"{cs.EMAIL_DESTINATION}: is not verified", out)

    def test_the_destination_comes_from_the_environment_or_env(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(f"{cs.EMAIL_DESTINATION}={DESTINATION}\n", encoding="utf-8")
            with mock.patch.object(cs, "ENV_FILE", env_file), \
                    mock.patch.dict("os.environ", {cs.EMAIL_DESTINATION: ""}):
                self.assertEqual(cs.variable(cs.EMAIL_DESTINATION), DESTINATION)

    def test_its_names_are_kept_apart_from_the_tools(self):
        # The tool refuses a .env holding a name it does not know, so these live in .env.infra,
        # which git ignores, so no emulator's copy of the checkout takes the token.
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(cs.ENV_FILE, root / ".env.infra")
        example = cs.credentials.parse((root / ".env.infra.example").read_text(encoding="utf-8"))
        kept = {f"API_{name}" for name, how in cs.API_SECRETS.items() if how == "kept"}
        self.assertEqual(set(example), {"CLOUDFLARE_API_TOKEN", cs.EMAIL_DESTINATION, cs.EMAIL_FORWARD, *kept})
        self.assertFalse(set(example) & (set(cs.credentials.KEYS) | set(cs.credentials.RENAMED)))
        self.assertEqual(subprocess.run(["git", "check-ignore", "-q", ".env.infra"], cwd=root).returncode, 0)

    def test_dnssec_is_turned_on_and_pending_is_waited_for(self):
        fake = FakeCloudflare()
        fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09T18:39:03Z"}]
        code, out = run(fake)
        self.assertIn("DNSSEC: is disabled", out)
        run(fake, "apply")
        self.assertIn(("PATCH", f"{ZONE}/dnssec", {"status": "active"}), fake.sent)
        # Turned on, it waits for Cloudflare to put the DS record in .com: nothing to send.
        fake.dnssec = "pending"
        fake.sent.clear()
        code, out = run(fake, "apply")
        self.assertEqual((code, fake.sent), (1, []))
        self.assertIn("DNSSEC: left - wait: Cloudflare, the registrar, adds the DS record", out)

    def test_caa_lets_only_cloudflares_certificate_authorities_issue(self):
        fake = FakeCloudflare()
        fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09T18:39:03Z"}]
        run(fake, "apply")
        made = [body for method, path, body in fake.sent if path == f"{ZONE}/dns_records" and body["type"] == "CAA"]
        self.assertEqual(sorted(body["data"]["value"] for body in made), ["letsencrypt.org", "pki.goog", "ssl.com"])
        self.assertTrue(all("content" not in body and body["data"]["tag"] == "issue" for body in made))
        # Listed back as Cloudflare writes them, they are no difference; one more by hand is.
        self.assertEqual(run(fake)[0], 0)
        fake.dns.append({"id": "h2", "type": "CAA", "name": cs.ZONE, "content": '0 issue "example.org"', "meta": {}})
        code, out = run(fake)
        self.assertIn(f"""CAA {cs.ZONE} '0 issue "example.org"': is not written down""", out)

    def test_the_workflow_only_checks(self):
        # Settings are applied by hand; the workflow's token can only read them.
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/cloudflare-settings.yml").read_text(
            encoding="utf-8")
        self.assertNotIn(" apply", workflow.split("\nname:", 1)[1])
        self.assertIn("run: python3 tools/repo/cloudflare_settings.py ci\n", workflow)
        self.assertIn("secrets.CLOUDFLARE_READ_TOKEN", workflow)
        self.assertIn('- cron: "0 6 * * *"', workflow, "checked daily, so drift shows within a day")
        for name in (cs.EMAIL_DESTINATION, cs.EMAIL_FORWARD):
            self.assertIn(f"{name}: ${{{{ secrets.{name} }}}}", workflow)


def deploy_token(zone=("Zone Read", "Workers Routes Write", "DNS Write"), workers=("t1", "t2")):
    """The deploy token as Cloudflare lists it: the zone by its id, Workers by their tags."""
    return {"name": cs.DEPLOY_TOKEN, "status": "active", "policies": [
        {"effect": "allow", "permission_groups": [{"name": name} for name in zone],
         "resources": {"com.cloudflare.api.account.zone.z1": "*"}},
        {"effect": "allow", "permission_groups": [{"name": "Individual Workers Editor"}],
         "resources": {"com.cloudflare.api.account.a1": {
             f"com.cloudflare.edge.worker.script.{tag}": "*" for tag in workers}}}]}


class DeployTokenTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCloudflare()
        self.fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09T18:39:03Z"}]
        run(self.fake, "apply")
        self.fake.sent.clear()

    def test_not_checked_by_a_token_that_cannot_read_tokens(self):
        code, out = run(self.fake)
        self.assertEqual(code, 0)
        self.assertIn(f"{cs.DEPLOY_TOKEN}: not checked", out)

    def test_as_written_it_is_no_difference(self):
        self.fake.tokens = [deploy_token(), {"name": "another", "status": "active", "policies": []}]
        code, out = run(self.fake)
        self.assertEqual(code, 0, out)
        self.assertNotIn(cs.DEPLOY_TOKEN, out)

    def test_as_written_it_is_no_difference_whatever_the_hash_order(self):
        # A set's order follows Python's hash seed, which differs run to run:
        # a comparison that leant on it failed now and then.
        import os
        import subprocess
        import sys
        root = Path(__file__).resolve().parents[2]
        code = ("from tests.repo.test_cloudflare_settings import DESTINATION, FakeCloudflare, deploy_token, run\n"
                "fake = FakeCloudflare(); fake.addresses = [{'email': DESTINATION, 'verified': '2026-11-09'}]\n"
                "run(fake, 'apply'); fake.tokens = [deploy_token()]\n"
                "print(run(fake)[0])")
        for seed in range(24):
            done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                                  env={**os.environ, "PYTHONHASHSEED": str(seed)}, check=True)
            self.assertEqual(done.stdout.strip(), "0", f"PYTHONHASHSEED={seed}")

    def test_a_wider_one_is_named_and_left_to_the_dashboard(self):
        self.fake.tokens = [deploy_token(zone=("Zone Read", "Workers Routes Write", "DNS Write",
                                               "Zone Settings Write"), workers=("t1", "t2", "t3"))]
        code, out = run(self.fake)
        self.assertEqual(code, 1)
        self.assertIn("Zone Settings Write", out)
        self.assertIn("another-site", out)
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn(f"{cs.DEPLOY_TOKEN}: left - change it in the dashboard", out)

    def test_a_missing_one_is_a_difference(self):
        self.fake.tokens = []
        code, out = run(self.fake)
        self.assertEqual(code, 1)
        self.assertIn("allows nothing (no such token)", out)


class CiTokenReachTests(unittest.TestCase):
    """`ci`: CI's read token sees this zone and account alone, no Workers, and
    is refused what CI_REFUSED lists."""

    def setUp(self):
        self.fake = FakeCloudflare()
        self.fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09T18:39:03Z"}]
        run(self.fake, "apply")
        self.fake.sent.clear()
        self.fake.workers = []   # as CI's token sees them

    def test_a_token_that_sees_no_more_passes(self):
        self.assertEqual(run(self.fake, "ci")[0], 0)

    def test_one_that_sees_another_zone_fails(self):
        self.fake.other_zones = ["example.com"]
        code, out = run(self.fake, "ci")
        self.assertEqual(code, 1)
        self.assertIn("can see other zones (example.com)", out)
        # Not asked of the apply token, used by hand.
        self.assertEqual(run(self.fake)[0], 0)

    def test_one_that_sees_another_account_or_a_worker_fails(self):
        self.fake.accounts = 2
        self.fake.workers = ["firmware-liberator"]
        code, out = run(self.fake, "ci")
        self.assertEqual(code, 1)
        self.assertIn("can see 2 accounts", out)
        self.assertIn("can see the Workers ['firmware-liberator']: it should see none", out)

    def test_each_read_it_should_be_refused_fails_where_it_works(self):
        for what, template in cs.CI_REFUSED[1:]:   # the tokens list has a test of its own
            with self.subTest(what):
                self.fake.readable = {template.format(account="a1", zone="z1")}
                code, out = run(self.fake, "ci")
                self.assertEqual(code, 1)
                self.assertIn(f"can read {what}: it should be refused", out)

    def test_one_that_reads_the_accounts_tokens_fails(self):
        self.fake.tokens = [deploy_token()]
        code, out = run(self.fake, "ci")
        self.assertEqual(code, 1)
        self.assertIn("can read the account's API tokens", out)
        self.assertEqual(self.fake.sent, [])



class DeployTokenReachTests(unittest.TestCase):
    """`deploy-token`, as the website workflow runs it with the deploy token:
    this zone and account, its two Workers, and no more."""

    def setUp(self):
        self.fake = FakeCloudflare()
        self.fake.workers = sorted(cs.DEPLOY_WORKERS)

    def test_a_token_that_sees_its_own_passes_and_sets_nothing(self):
        code, out = run(self.fake, "deploy-token")
        self.assertEqual((code, self.fake.sent), (0, []))
        self.assertIn("sees firmwareliberator.com, its Workers and no more", out)

    def test_one_that_sees_another_worker_or_zone_fails(self):
        self.fake.workers.append("another-site")
        self.fake.other_zones = ["example.com"]
        code, out = run(self.fake, "deploy-token")
        self.assertEqual(code, 1)
        self.assertIn("another-site", out)
        self.assertIn("can see other zones (example.com)", out)

    def test_each_read_it_should_be_refused_fails_where_it_works(self):
        for what, template in cs.DEPLOY_REFUSED:
            with self.subTest(what):
                self.fake.readable = {template.format(account="a1", zone="z1")}
                if template.endswith("/tokens"):
                    self.fake.tokens, self.fake.readable = [], set()
                code, out = run(self.fake, "deploy-token")
                self.assertEqual(code, 1)
                self.assertIn(f"can read {what}: it should be refused", out)
                self.fake.tokens = None

    def test_what_it_edits_it_may_read(self):
        # Its Workers' custom domains are DNS records; its Workers are its to edit.
        self.fake.readable = {"/zones/z1/dns_records",
                              "/accounts/a1/workers/scripts/firmware-liberator-api/settings"}
        self.assertEqual(run(self.fake, "deploy-token")[0], 0)

    def test_the_website_workflow_runs_it_after_both_deploys(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/website.yml").read_text(encoding="utf-8")
        job = workflow.split("\n  deploy-token:\n", 1)[1]
        self.assertIn("needs: [deploy, deploy-api]", job)
        self.assertIn("run: python3 tools/repo/cloudflare_settings.py deploy-token", job)
        self.assertIn("CLOUDFLARE_API_TOKEN: ${{ secrets.CLOUDFLARE_DEPLOY_TOKEN }}", job)


class DatabaseTests(unittest.TestCase):
    """The API's D1 database: made, bound by its id, and every migration run."""

    def setUp(self):
        self.fake = FakeCloudflare()
        self.fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09"}]
        run(self.fake, "apply")
        self.fake.sent.clear()

    def test_a_migration_not_yet_run_is_run_alone_and_recorded(self):
        newest = cs.migration_files()[-1]
        self.fake.migrations = self.fake.migrations[:-1]
        code, out = run(self.fake)
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn(f"migration {newest}: has not been run", out)
        code, out = run(self.fake, "apply")
        self.assertEqual(code, 0, out)
        self.assertEqual([path for _, path, _ in self.fake.sent], [QUERY])
        self.assertEqual(self.fake.migrations, cs.migration_files())

    def test_a_database_never_migrated_is_migrated(self):
        self.fake.migrations = None
        self.assertEqual(run(self.fake, "apply")[0], 0)
        self.assertEqual(self.fake.migrations, cs.migration_files())

    def test_migrations_run_out_of_their_order_are_left_to_the_maintainer(self):
        self.fake.migrations = ["0001_renamed.sql"]
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("a migration was changed, removed or renamed after it was run", out)

    def test_a_binding_to_another_database_says_which_id_to_put_there(self):
        self.fake.bound = cs.D1_PLACEHOLDER
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("left - set its database_id to u1, add that id to tools/repo/leak-scan-allow", out)

    def test_made_and_migrated_in_one_apply_which_says_the_id_to_bind(self):
        fake = FakeCloudflare()
        fake.bound = cs.D1_PLACEHOLDER
        code, out = run(fake, "apply")
        self.assertEqual(code, 1)
        self.assertEqual(fake.migrations, cs.migration_files())
        self.assertIn("set its database_id to u1", out)

    def test_ci_never_asks_for_the_database(self):
        fake = FakeCloudflare()

        def no_d1(path, *args, **kwargs):
            self.assertNotIn("/d1/database?", path)
            self.assertNotEqual(path, QUERY)
            return fake(path, *args, **kwargs)
        code, out = run(no_d1, "ci")
        self.assertIn(f"D1 {cs.D1_DATABASE}: not checked", out)

    def test_what_apply_sends_runs_as_sqlite_runs_it(self):
        # The SQL a migration is sent as, run where D1's engine runs: it makes
        # what it should, records itself once, and runs again harmlessly
        # but for the record, which is why a recorded one is never sent again.
        db = sqlite3.connect(":memory:")
        for name in cs.migration_files():
            fake = FakeCloudflare()
            fake.databases = [{"name": cs.D1_DATABASE, "uuid": "u1"}]
            fake.migrations = [earlier for earlier in cs.migration_files() if earlier < name]
            with mock.patch.object(cs, "api", fake), mock.patch.object(cs, "bound_database_id", lambda: "u1"):
                changes = [change for _, _, change in cs.database_differences("a1")]
            sql = changes[0][2]["sql"]
            db.executescript(sql)
            body = sql[:sql.rindex("INSERT INTO d1_migrations")]
            db.executescript(body)   # the migration itself, again
        self.assertEqual([row[0] for row in db.execute("SELECT name FROM d1_migrations ORDER BY id")],
                         cs.migration_files())

    def test_the_api_worker_binds_the_database_by_name(self):
        self.assertRegex(cs.bound_database_id(), r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

    def test_a_migration_badly_named_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "0001_ok.sql").write_text("", encoding="utf-8")
            (Path(folder) / "0002_it's.sql").write_text("", encoding="utf-8")
            with mock.patch.object(cs, "MIGRATIONS", Path(folder)), self.assertRaises(cs.SettingsError):
                cs.migration_files()


class EmailSendingTests(unittest.TestCase):
    """The domain the API Worker's email is sent from, onboarded by apply."""

    def setUp(self):
        self.fake = FakeCloudflare()
        self.fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09"}]

    def test_apply_onboards_the_zone_itself_and_makes_the_records_it_wants(self):
        code, out = run(self.fake)
        self.assertIn(f"Email Sending for {cs.ZONE}: is not onboarded", out)
        run(self.fake, "apply")
        self.assertEqual(self.fake.sending, [{"tag": "s1", "name": cs.ZONE, "enabled": True}])
        made = [(r["type"], r["name"]) for r in self.fake.dns]
        for record in SENDING_DNS[:3]:
            self.assertIn((record["type"], record["name"]), made)
        self.assertEqual(run(self.fake)[0], 0, "then nothing differs, the new records expected")

    def test_the_zones_one_dmarc_record_stays_alone(self):
        # Two DMARC records void both: the zone keeps its own (DNS_RECORDS).
        run(self.fake, "apply")
        dmarc = [r for r in self.fake.dns if r["name"] == f"_dmarc.{cs.ZONE}"]
        self.assertEqual(len(dmarc), 1)
        self.assertIn("p=quarantine", dmarc[0]["content"])

    def test_a_record_it_wants_gone_missing_is_found(self):
        run(self.fake, "apply")
        self.fake.dns = [r for r in self.fake.dns if r["name"] != f"cf-bounce._domainkey.{cs.ZONE}"]
        code, out = run(self.fake)
        self.assertEqual(code, 1)
        self.assertIn(f"TXT cf-bounce._domainkey.{cs.ZONE} 'v=DKIM1; p=CCCCDDDD': is missing", out)

    def test_one_turned_off_is_left_to_the_dashboard(self):
        self.fake.sending = [{"tag": "s1", "name": cs.ZONE, "enabled": False}]
        code, out = run(self.fake, "apply")
        self.assertIn(f"Email Sending for {cs.ZONE}: left - enable it in the dashboard", out)


class ApiAndStatsTests(unittest.TestCase):
    """The API's firewall rule and secret, and the public figures' bucket."""

    def setUp(self):
        self.fake = FakeCloudflare()
        self.fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09"}]
        run(self.fake, "apply")
        self.fake.sent.clear()

    def test_the_api_takes_only_a_post_to_its_paths_and_a_get_of_the_removal_page(self):
        expression = cs.FIREWALL_RULES[0]["expression"]
        self.assertTrue(expression.startswith(f'(http.host eq "{cs.API_HOST}" and not '))
        for path in cs.API_PATHS:
            self.assertIn(f'"{path}"', expression)
        self.assertIn('(http.request.method in {"POST"} and http.request.uri.path in '
                      '{"/readout" "/wanted" "/notify" "/reports"})', expression)
        self.assertIn('(http.request.method in {"GET" "POST"} and http.request.uri.path in {"/confirm" "/forget"})',
                      expression)
        self.assertEqual(cs.FIREWALL_RULES[0]["action"], "block")

    def test_the_figures_are_cached_on_their_host_alone(self):
        rule = cs.CACHE_RULES[0]
        self.assertEqual(rule["expression"], f'(http.host eq "{cs.STATS_HOST}")')
        self.assertTrue(rule["action_parameters"]["cache"])

    def test_r2_dev_turned_on_by_hand_is_turned_off(self):
        self.fake.r2_dev = True
        code, out = run(self.fake)
        self.assertEqual(code, 1)
        self.assertIn("r2.dev address: is on: it should be off", out)
        run(self.fake, "apply")
        self.assertFalse(self.fake.r2_dev)

    def test_another_domain_for_the_figures_is_left_to_the_maintainer(self):
        self.fake.stats_domains.append({"domain": f"figures.{cs.ZONE}", "enabled": True})
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn(f"should be at {cs.STATS_HOST} alone", out)

    def test_the_figures_domains_own_dns_record_is_never_deleted(self):
        self.fake.dns.append({"id": "s1", "type": "CNAME", "name": cs.STATS_HOST, "content": "public.r2.dev",
                              "meta": {}})
        self.assertEqual(run(self.fake)[0], 0)

    def test_a_made_secret_not_set_is_made_by_apply_a_value_never_shown(self):
        self.fake.secrets = [name for name in cs.API_SECRETS if name != "SENDER_KEY"]
        code, out = run(self.fake, "apply")
        self.assertEqual(code, 0, out)
        [(method, path, body)] = self.fake.sent
        self.assertEqual((method, path, body["name"], body["type"]), ("PUT", SECRETS, "SENDER_KEY", "secret_text"))
        self.assertRegex(body["text"], r"^[0-9a-f]{64}$")
        self.assertNotIn(body["text"], out)
        self.assertIn("secret SENDER_KEY: set", out)
        self.assertEqual(run(self.fake)[0], 0, "then nothing differs")

    def test_a_kept_secret_is_set_from_env_infra_and_never_made(self):
        self.fake.secrets = [name for name in cs.API_SECRETS if name != "DEVICE_HASH_KEY_PERMANENT"]
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []), "with no API_DEVICE_HASH_KEY_PERMANENT, nothing is made up")
        self.assertIn("secret DEVICE_HASH_KEY_PERMANENT: left - put its value, kept privately, in .env.infra as API_DEVICE_HASH_KEY_PERMANENT", out)
        with mock.patch.dict("os.environ", {"API_DEVICE_HASH_KEY_PERMANENT": "a-made-up-kept-value"}):
            code, out = run(self.fake, "apply")
        self.assertEqual(code, 0, out)
        self.assertEqual([(body["name"], body["text"]) for _, _, body in self.fake.sent],
                         [("DEVICE_HASH_KEY_PERMANENT", "a-made-up-kept-value")])
        self.assertNotIn("a-made-up-kept-value", out)

    def test_the_alerts_address_is_set_from_the_email_destination_never_shown(self):
        self.fake.secrets = [name for name in cs.API_SECRETS if name != "ALERTS_RECIPIENT_EMAIL"]
        code, out = run(self.fake, "apply")
        self.assertEqual(code, 0, out)
        [(method, path, body)] = [sent for sent in self.fake.sent if sent[1] == SECRETS]
        self.assertEqual((method, body["name"], body["text"]), ("PUT", "ALERTS_RECIPIENT_EMAIL", DESTINATION))
        self.assertNotIn(DESTINATION, out)

    def test_the_alerts_address_waits_for_the_email_destination(self):
        self.fake.secrets = [name for name in cs.API_SECRETS if name != "ALERTS_RECIPIENT_EMAIL"]
        out = io.StringIO()
        with mock.patch.object(cs, "api", self.fake), redirect_stdout(out), \
                mock.patch.object(cs, "bound_database_id", lambda: self.fake.bound), \
                mock.patch.object(cs, "email_differences", lambda zone, account: []), \
                mock.patch.object(cs, "variable", side_effect=lambda name: (_ for _ in ()).throw(
                    cs.SettingsError(f"no {name}")) if name == cs.EMAIL_DESTINATION else "x"):
            cs.main(["apply"])
        self.assertIn(f"secret ALERTS_RECIPIENT_EMAIL: left - put the maintainer's address in .env.infra as {cs.EMAIL_DESTINATION}",
                      out.getvalue())

    def test_only_a_made_secret_is_rotated(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cs, "api", self.fake), redirect_stdout(out), mock.patch("sys.stderr", err):
            self.assertEqual(cs.main(["rotate", "DEVICE_HASH_KEY_PERMANENT"]), 2)
            self.assertEqual(self.fake.sent, [])
            self.assertIn("DEVICE_HASH_KEY_PERMANENT is kept, and must never change", err.getvalue())
            self.assertEqual(cs.main(["rotate", "SENDER_KEY"]), 0)
        [(_, _, body)] = self.fake.sent
        self.assertEqual(body["name"], "SENDER_KEY")
        self.assertNotIn(body["text"], out.getvalue())

    def test_before_the_worker_is_deployed_its_secrets_wait(self):
        self.fake.secrets = None
        code, out = run(self.fake)
        self.assertEqual(code, 1)
        self.assertIn("cannot be checked: the Worker is not there yet", out)

    def test_ci_does_not_ask_for_the_workers_secrets(self):
        def no_secrets(path, *args, **kwargs):
            self.assertNotEqual(path, SECRETS)
            return self.fake(path, *args, **kwargs)
        code, out = run(no_secrets, "ci")
        self.assertIn(f"{cs.API_WORKER} secrets: not checked", out)

    def test_the_worker_binds_the_figures_bucket_and_its_secrets_are_set_here(self):
        config = (Path(__file__).resolve().parents[2] / "website/api/wrangler.jsonc").read_text(encoding="utf-8")
        self.assertIn(f'"bucket_name": "{cs.STATS_BUCKET}"', config)
        self.assertNotIn("wrangler secret put", config, "set by cloudflare_settings.py apply, not by hand")
        for name in cs.API_SECRETS:
            self.assertIn(name, config)



class StoppingTheApiTests(unittest.TestCase):
    """The website workflow, run by hand, can deploy the API taking nothing."""

    def test_run_by_hand_it_can_deploy_the_api_switched_off(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/website.yml").read_text(encoding="utf-8")
        trigger = workflow[workflow.index("  workflow_dispatch:"):workflow.index("\npermissions:")]
        self.assertIn('options: ["on", "off"]', trigger)
        self.assertIn('default: "on"', trigger)
        job = workflow[workflow.index("\n  deploy-api:"):workflow.index("\n  deploy-token:")]
        self.assertIn("INGEST: ${{ inputs.ingest || 'on' }}", job, "a push deploys it on")
        self.assertIn('npx wrangler deploy --config api/wrangler.jsonc --var "INGEST:$INGEST"', job)
        self.assertNotIn("${{ inputs.ingest }}", job.split("run: |", 1)[1], "never pasted into the script")

    def test_the_switch_is_the_workers_own(self):
        root = Path(__file__).resolve().parents[2]
        self.assertIn('"INGEST": "on"', (root / "website/api/wrangler.jsonc").read_text(encoding="utf-8"))
        self.assertIn('if (env.INGEST !== "on") {', (root / "website/api/index.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
