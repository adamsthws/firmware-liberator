import copy
import datetime as dt
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
ADMIN_SECRETS = f"/accounts/a1/workers/scripts/{cs.ADMIN_WORKER}/secrets"
ACCESS = "/accounts/a1/access"
QUERY = f"{DATABASES}/u1/query"
ADDRESSES = "/accounts/a1/email/routing/addresses"
ANALYTICS = "/accounts/a1/rum/site_info"
ALL_MEASURED = {"id": "ru1", "host": "*", "paths": ["*"], "inclusive": True, "is_paused": False, "priority": 1000}
DESTINATION = "owner@example.com"
FORWARDED = ["hello", "press"]
APPLY_VALUE = "made-up-apply-token-value-0123456789abcdef"
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
        self.tokens_page = 50   # how many Cloudflare lists a page
        self.other_zones = []   # zones besides this one the token can see
        self.accounts = 1   # how many accounts the token can see
        # The Workers the token can see: Cloudflare lists those alone. The apply token sees them all.
        self.workers = ["firmware-liberator", "firmware-liberator-api", "firmware-liberator-admin", "another-site"]
        self.readable = set()   # of CI_REFUSED's paths, those the token may read
        self.routing = False
        # The domain's forwarding, as `email` leaves it: kept by Email Routing alone.
        self.email_rules = [{"id": f"e{n}", **rule}
                            for n, rule in enumerate(cs.email_rules(DESTINATION, FORWARDED), 1)]
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
        self.admin_secrets = list(cs.ADMIN_SECRETS)   # the admin Worker's, likewise
        # Access, as apply leaves it: the team, GitHub sign-in, the admin application and its one policy.
        self.organisation = {"auth_domain": "team1.cloudflareaccess.com"}
        self.idps = [{"id": "g1", "name": "GitHub", "type": "github"}]
        self.apps = [{"id": "app1", "aud": "aud1", **cs.admin_app_body("g1")}]
        self.policies = {"app1": [{"id": "p1", **cs.admin_policy_body("g1", DESTINATION)}]}
        self.admin_vars = {"ACCESS_TEAM": "team1", "ACCESS_AUD": "aud1", "ACCOUNT_ID": "a1"}
        self.sending = []   # the zone's domains onboarded for Email Service's sending
        # The account's Web Analytics sites: the zone's one, as the pages' beacon tag names it.
        # Each starts with the one rule Cloudflare makes, every host and path measured.
        self.analytics = [{"site_tag": "t1", "site_token": cs.beacon_token(), "auto_install": False,
                           "rules": [copy.deepcopy(ALL_MEASURED)],
                           "ruleset": {"id": "rs1", "zone_tag": "z1", "zone_name": cs.ZONE, "enabled": False}}]
        # Beyond what is written there: zone rulesets in phases it does not keep (Cloudflare's own
        # managed ones are always listed besides), Worker routes, and the Workers' custom domains,
        # as the wrangler.jsonc files make them.
        self.other_rulesets = []
        self.routes = []
        self.refused = set()   # paths this token is refused: a 403

        self.custom_domains = [{"id": f"cd{n}", "hostname": host, "service": worker, "zone_name": cs.ZONE}
                               for n, (host, worker) in enumerate(sorted(cs.custom_domains()))]
        self.sent = []
        self.ids = iter(range(100, 1000))

    @property
    def redirects(self):
        return self.rulesets.get(ENTRYPOINT)

    def __call__(self, path, method="GET", body=None, ok_missing=False, ok_forbidden=False, whole=False):
        if path in self.refused:
            if method == "GET" and ok_forbidden:
                return None
            raise cs.SettingsError(f"{method} {path}: HTTP 403: Authentication error", 403)
        if path == QUERY and body["sql"] == "SELECT name FROM d1_migrations ORDER BY id":   # a read
            if self.migrations is None:
                raise cs.SettingsError(f"POST {QUERY}: HTTP 400: no such table: d1_migrations: SQLITE_ERROR")
            return [{"results": [{"name": name} for name in self.migrations], "success": True}]
        if method != "GET":
            self.sent.append((method, path, body))
            if method == "DELETE" and path.startswith(f"{ZONE}/rulesets/"):
                self.other_rulesets = [r for r in self.other_rulesets if r["id"] != path.rsplit("/", 1)[1]]
            elif method == "DELETE" and path.startswith(f"{ZONE}/workers/routes/"):
                self.routes = [r for r in self.routes if r["id"] != path.rsplit("/", 1)[1]]
            elif method == "DELETE" and path.startswith("/accounts/a1/workers/domains/"):
                self.custom_domains = [d for d in self.custom_domains if d["id"] != path.rsplit("/", 1)[1]]
            elif path == f"{ZONE}/dns_records":
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
            elif path == ANALYTICS:
                self.analytics.append({"site_tag": "t2", "site_token": "f" * 32, "rules": [copy.deepcopy(ALL_MEASURED)],
                                       "ruleset": {"id": "rs2", "zone_tag": body["zone_tag"]}, **body})
                return copy.deepcopy(self.analytics[-1])
            elif path.startswith(f"{ANALYTICS}/"):
                site = next(s for s in self.analytics if s["site_tag"] == path.rsplit("/", 1)[1])
                site.update(body)
            elif path == SENDING:
                self.sending.append({"tag": "s1", "name": body["name"], "enabled": True})
                return copy.deepcopy(self.sending[-1])
            elif path == SECRETS and method == "PUT":
                self.secrets = [name for name in self.secrets or [] if name != body["name"]] + [body["name"]]
            elif path == ADMIN_SECRETS and method == "PUT":
                self.admin_secrets = [name for name in self.admin_secrets or [] if name != body["name"]] + [body["name"]]
            elif path == f"{ACCESS}/identity_providers":
                self.idps.append({"id": f"g{next(self.ids)}", "name": body["name"], "type": body["type"],
                                  "config": copy.deepcopy(body["config"])})
            elif path == f"{ACCESS}/apps" and method == "POST":
                self.apps.append({"id": f"app{next(self.ids)}", "aud": f"aud{next(self.ids)}", **copy.deepcopy(body)})
                self.policies[self.apps[-1]["id"]] = []
                return copy.deepcopy(self.apps[-1])
            elif re.fullmatch(f"{ACCESS}/apps/[^/]+", path):
                app = next(app for app in self.apps if app["id"] == path.rsplit("/", 1)[1])
                app.update(copy.deepcopy(body))
            elif re.fullmatch(f"{ACCESS}/apps/[^/]+/policies", path):
                self.policies[path.split("/")[-2]].append({"id": f"p{next(self.ids)}", **copy.deepcopy(body)})
            elif re.fullmatch(f"{ACCESS}/apps/[^/]+/policies/[^/]+", path):
                app = path.split("/")[-3]
                self.policies[app] = [{"id": policy["id"], **copy.deepcopy(body)} if policy["id"] == path.rsplit("/", 1)[1]
                                      else policy for policy in self.policies[app]]
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
        if path.startswith("/accounts/a1/tokens?per_page=50&page="):
            assert ok_forbidden and whole
            if self.tokens is None:
                return None
            page, size = int(path.rsplit("=", 1)[1]), self.tokens_page
            return {"result": copy.deepcopy(self.tokens[(page - 1) * size:page * size]),
                    "result_info": {"page": page, "total_pages": max(1, -(-len(self.tokens) // size))}}
        if path in {template.format(account="a1", zone="z1") for _, template in cs.CI_REFUSED + cs.DEPLOY_REFUSED}:
            assert ok_forbidden
            return {} if path in self.readable else None
        if path == f"{ANALYTICS}/list?per_page=100":   # as Cloudflare lists them: without their rules
            return [{k: v for k, v in site.items() if k != "rules"} for site in self.analytics]
        if path.startswith(f"{ANALYTICS}/"):
            return copy.deepcopy(next(s for s in self.analytics if s["site_tag"] == path.rsplit("/", 1)[1]))
        if path == f"{STATS}/domains/custom":
            return {"domains": copy.deepcopy(self.stats_domains)}
        if path == f"{STATS}/domains/managed":
            return {"enabled": self.r2_dev}
        if path == SECRETS:
            assert ok_missing
            return None if self.secrets is None else [{"name": name, "type": "secret_text"} for name in self.secrets]
        if path == ADMIN_SECRETS:
            assert ok_missing
            return None if self.admin_secrets is None else [{"name": name, "type": "secret_text"}
                                                            for name in self.admin_secrets]
        if path == f"{ACCESS}/organizations":
            assert ok_missing
            if self.organisation == "forbidden":
                raise cs.SettingsError(f"GET {path}: HTTP 403: Authentication error", 403)
            return copy.deepcopy(self.organisation)
        if path == f"{ACCESS}/identity_providers":
            return copy.deepcopy(self.idps)
        if path == f"{ACCESS}/apps":
            return copy.deepcopy(self.apps)
        if re.fullmatch(f"{ACCESS}/apps/[^/]+/policies", path):
            return copy.deepcopy(self.policies[path.split("/")[-2]])
        if path == f"{DATABASES}?name={cs.D1_DATABASE}":
            return copy.deepcopy(self.databases)
        if path == f"{ZONE}/rulesets":
            return [{"id": "m1", "kind": "managed", "phase": "ddos_l7", "name": "DDoS L7 ruleset"},
                    *({"id": f"z{n}", "kind": "zone", "phase": entrypoint.split("/")[-2], "name": "default"}
                      for n, entrypoint in enumerate(self.rulesets)),
                    *copy.deepcopy(self.other_rulesets)]
        if path == f"{ZONE}/workers/routes":
            assert ok_forbidden
            return copy.deepcopy(self.routes)
        if path == BUCKETS:
            assert ok_forbidden
            return {"buckets": [{"name": name} for name in sorted(self.buckets)]}
        if path == f"/accounts/a1/workers/domains?zone_name={cs.ZONE}":
            return copy.deepcopy(self.custom_domains)
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


def run(fake, *args, answers=None):
    """main against `fake`; with `answers`, in a terminal where each hidden
    prompt is given the next of them, and else with none to ask at."""
    out = io.StringIO()
    stdin = io.StringIO()
    if answers is not None:
        stdin.isatty = lambda: True
    # The apply token, as asked for and found to be as TOKENS has it (AskedTokenTests).
    with mock.patch.object(cs, "api", fake), redirect_stdout(out), mock.patch("sys.stdin", stdin), \
            mock.patch.object(cs, "asked_token", lambda: APPLY_VALUE), \
            mock.patch.object(cs.getpass, "getpass", side_effect=list(answers or [])), \
            mock.patch.object(cs, "bound_database_id", lambda config=None: fake.bound), \
            mock.patch.object(cs, "admin_vars", lambda: copy.deepcopy(getattr(fake, "admin_vars", {}))), \
            mock.patch.object(cs, "ENV_FILE", Path("/nonexistent/.env.infra")):
        code = cs.main(list(args))
    return code, out.getvalue()


class CloudflareSettingsTests(unittest.TestCase):
    def test_check_only_says_what_differs(self):
        fake = FakeCloudflare()
        code, out = run(fake)
        self.assertEqual((code, fake.sent), (1, []))
        self.assertIn("always_use_https: is off, should be on", out)
        self.assertIn("redirects: are [], should be ['www-to-apex', 'issues-to-github', 'issue-for-a-report']", out)
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

    def test_the_short_issues_address_goes_to_the_repositorys_issues(self):
        from tools.repo import github_settings
        self.assertEqual(cs.ISSUES, f"https://github.com/{github_settings.REPOSITORY}/issues")
        rule = next(rule for rule in cs.REDIRECTS if rule["ref"] == "issues-to-github")
        self.assertEqual(rule["action_parameters"]["from_value"]["target_url"], {"value": cs.ISSUES})
        self.assertIn(f'http.host eq "{cs.ZONE}"', rule["expression"])
        self.assertIn(f'"{cs.ISSUES_PATH}"', rule["expression"])
        from tools.autoflash import report
        self.assertEqual(report.ISSUES_URL, f"https://{cs.ZONE}{cs.ISSUES_PATH}")   # what the tool shows

    def test_the_short_issues_address_with_a_reports_id_opens_the_form_naming_it(self):
        rule = next(rule for rule in cs.REDIRECTS if rule["ref"] == "issue-for-a-report")
        target = rule["action_parameters"]["from_value"]["target_url"]["expression"]
        self.assertIn(f'"{cs.ISSUES}/new?template={cs.ISSUE_FORM}&report=${{1}}"', target)
        form = (Path(__file__).resolve().parents[2] / ".github/ISSUE_TEMPLATE" / cs.ISSUE_FORM).read_text(encoding="utf-8")
        self.assertIn("    id: report\n", form, "the field the id fills in")
        # The bare address is the rule before's: this one needs something after the slash.
        self.assertIn(f'wildcard "{cs.ISSUES_PATH}/*"', rule["expression"])
        self.assertIn(f'ne "{cs.ISSUES_PATH}/"', rule["expression"])
        refs = [rule["ref"] for rule in cs.REDIRECTS]
        self.assertLess(refs.index("issues-to-github"), refs.index("issue-for-a-report"))

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
            # Set here: a command run before, in this process, leaves its own.
            with mock.patch.object(cs, "ENV_FILE", env_file), \
                    mock.patch.object(cs, "TOKEN_VARIABLE", "CLOUDFLARE_SETTINGS_APPLY_TOKEN"), \
                    mock.patch.dict("os.environ", {"CLOUDFLARE_SETTINGS_APPLY_TOKEN": ""}):
                with self.assertRaisesRegex(cs.SettingsError,
                                            "no CLOUDFLARE_SETTINGS_APPLY_TOKEN: in the environment or .env.infra"):
                    cs.token()
                env_file.write_text("CLOUDFLARE_SETTINGS_APPLY_TOKEN=from-the-file\n", encoding="utf-8")
                self.assertEqual(cs.token(), "from-the-file")
                with mock.patch.dict("os.environ", {"CLOUDFLARE_SETTINGS_APPLY_TOKEN": "from-the-environment"}):
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

    def test_forwarding_is_held_to_its_shape_and_no_address_is_shown(self):
        fake = FakeCloudflare()
        fake.addresses = [{"email": DESTINATION.upper(), "verified": "2026-11-09T18:39:03Z"}]
        self.assertEqual(run(fake, "apply")[0], 0)
        fake.email_rules.reverse()   # listed in another order: no difference
        self.assertEqual(run(fake)[0], 0)
        # One sent elsewhere, one that drops, and a catch-all turned on.
        fake.email_rules[0]["actions"][0]["value"] = ["someone@example.org"]
        fake.email_rules.append({"id": "e9", "name": "sales", "matchers": [{"type": "literal", "field": "to",
                                 "value": f"sales@{cs.ZONE}"}], "actions": [{"type": "drop"}], "enabled": True})
        fake.catch_all["enabled"] = True
        fake.addresses.append({"email": "someone@example.org", "verified": None})
        fake.sent.clear()
        code, out = run(fake, "apply")
        self.assertEqual(code, 1)
        self.assertIn("email rule 3: does not forward one of the domain's addresses to one address", out)
        self.assertIn("email rules: forward to 2 addresses: there should be one", out)
        self.assertIn("email catch-all: forwards as written nowhere", out)
        for private in ("@example.", "hello", "press", "sales"):
            self.assertNotIn(private, out.lower())
        # Which, and to whom, only `email` changes; the catch-all apply puts back.
        self.assertEqual(fake.sent, [("PUT", f"{ZONE}/email/routing/rules/catch_all", cs.EMAIL_CATCH_ALL)])

    def test_no_forwarding_is_left_to_email_which_asks(self):
        fake = FakeCloudflare()
        fake.email_rules = []
        fake.secrets = [name for name in cs.API_SECRETS if name != "ALERTS_RECIPIENT_EMAIL"]
        code, out = run(fake, "apply")
        self.assertEqual(code, 1)
        self.assertIn("email forwarding: left - run python3 tools/repo/cloudflare_settings.py email in a terminal", out)
        self.assertIn("secret ALERTS_RECIPIENT_EMAIL: left - set the domain's mail forwarding first", out)

    def test_email_sets_the_forwarding_asked_for_and_the_alerts_address_never_showing_them(self):
        fake = FakeCloudflare()
        fake.email_rules = [{"id": "old", "name": "", "matchers": [{"type": "literal", "field": "to",
                             "value": f"old@{cs.ZONE}"}], "actions": [{"type": "forward", "value": ["x@example.net"]}],
                             "enabled": True}]
        code, out = run(fake, "email", answers=["\x1b[200~New@Example.com\x1b[201~", "Contact, press ,contact"])
        self.assertEqual(code, 0, out)
        self.assertEqual(sorted((r["matchers"][0]["value"], r["actions"][0]["value"]) for r in fake.email_rules),
                         [(f"contact@{cs.ZONE}", ["new@example.com"]), (f"press@{cs.ZONE}", ["new@example.com"])])
        self.assertTrue(fake.routing)
        self.assertEqual(fake.addresses, [{"email": "new@example.com", "verified": None}])
        self.assertIn(("PUT", SECRETS, {"name": "ALERTS_RECIPIENT_EMAIL", "text": "new@example.com",
                                        "type": "secret_text"}), fake.sent)
        for private in ("example.com", "contact", "press"):
            self.assertNotIn(private, out.lower())

    def test_email_changes_nothing_given_no_address_or_no_terminal(self):
        for args in ({"answers": ["not an address", "contact"]}, {"answers": ["new@example.com", "a b"]}, {}):
            with self.subTest(args=args):
                fake = FakeCloudflare()
                with mock.patch("sys.stderr", io.StringIO()):
                    code, _ = run(fake, "email", **args)
                self.assertNotEqual(code, 0)
                self.assertEqual(fake.sent, [])

    def test_an_unverified_destination_is_left_to_its_owner(self):
        fake = FakeCloudflare()
        run(fake, "apply")
        fake.sent.clear()
        code, out = run(fake, "apply")
        self.assertEqual((code, fake.sent), (1, []))
        self.assertIn("the address mail is forwarded to: is not verified", out)

    def test_its_names_are_kept_apart_from_the_tools(self):
        # The tool refuses a .env holding a name it does not know, so these live in .env.infra,
        # which git ignores, so no emulator's copy of the checkout takes the token.
        root = Path(__file__).resolve().parents[2]
        self.assertEqual(cs.ENV_FILE, root / ".env.infra")
        example = cs.credentials.parse((root / ".env.infra.example").read_text(encoding="utf-8"))
        tokens = {spec["variable"] for spec in cs.TOKENS.values() if spec["kept"] == "env"}
        # What a tool asks for at a hidden prompt has no place in it.
        self.assertEqual(set(example), {"ACCESS_ADMIN_EMAIL", *tokens})
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
        self.assertIn("CLOUDFLARE_SETTINGS_CHECK_TOKEN: ${{ secrets.CLOUDFLARE_SETTINGS_CHECK_TOKEN }}", workflow)
        self.assertIn('- cron: "0 6 * * *"', workflow, "checked daily, so drift shows within a day")
        self.assertNotIn("EMAIL", workflow, "the forwarding is held to its shape, and needs no address")


WORKERS = ["firmware-liberator", "firmware-liberator-api", "firmware-liberator-admin", "another-site"]


def listed(key, extra=(), workers=WORKERS):
    """A token of TOKENS as Cloudflare lists it once made: the zone by its id,
    the account by its, Workers by their tags as FakeCloudflare gives them;
    `extra` permissions on the zone besides."""
    spec = cs.TOKENS[key]
    tags = {name: f"t{n + 1}" for n, name in enumerate(workers)}
    resources = {"zone": {"com.cloudflare.api.account.zone.z1": "*"}, "account": {"com.cloudflare.api.account.a1": "*"},
                 "workers": {"com.cloudflare.api.account.a1": {
                     f"com.cloudflare.edge.worker.script.{tags[name]}": "*" for name in sorted(cs.DEPLOY_WORKERS)}}}
    policies = [{"effect": "allow", "resources": resources[scope],
                 "permission_groups": [{"name": name} for name in [*spec[scope], *(extra if scope == "zone" else ())]]}
                for scope in ("zone", "account", "workers") if spec.get(scope)]
    return {"id": f"id-{key}", "name": cs.TOKEN_PREFIX + key, "status": "active", "policies": policies}


class TokenTests(unittest.TestCase):
    """`apply` checks each token TOKENS lists against what it says, and finds
    any other of the project's."""

    def setUp(self):
        self.fake = FakeCloudflare()
        self.fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09T18:39:03Z"}]
        self.fake.workers = list(WORKERS)
        run(self.fake, "apply")
        self.fake.sent.clear()
        self.fake.tokens = [listed(key) for key in cs.TOKENS] + [{"name": "another-site", "status": "active",
                                                                   "policies": []}]

    def test_not_checked_by_a_token_that_cannot_read_tokens(self):
        self.fake.tokens = None
        code, out = run(self.fake)
        self.assertEqual(code, 0)
        self.assertIn("API tokens: not checked", out)

    def test_as_written_it_is_no_difference(self):
        code, out = run(self.fake)
        self.assertEqual(code, 0, out)
        self.assertNotIn(cs.TOKEN_PREFIX + "workers-deploy", out)

    def test_as_written_it_is_no_difference_whatever_the_hash_order(self):
        # A set's order follows Python's hash seed, which differs run to run:
        # a comparison that leant on it failed now and then.
        import os
        import sys
        root = Path(__file__).resolve().parents[2]
        code = ("from tests.repo.test_cloudflare_settings import DESTINATION, FakeCloudflare, WORKERS, listed, run\n"
                "from tools.repo import cloudflare_settings as cs\n"
                "fake = FakeCloudflare(); fake.addresses = [{'email': DESTINATION, 'verified': '2026-11-09'}]\n"
                "fake.workers = list(WORKERS); run(fake, 'apply'); fake.tokens = [listed(k) for k in cs.TOKENS]\n"
                "print(run(fake)[0])")
        for seed in range(24):
            done = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True,
                                  env={**os.environ, "PYTHONHASHSEED": str(seed)}, check=True)
            self.assertEqual(done.stdout.strip(), "0", f"PYTHONHASHSEED={seed}")

    def test_a_wider_one_is_named_and_left_to_be_remade(self):
        self.fake.tokens[2] = listed("workers-deploy", extra=["Zone Settings Write"])
        self.fake.tokens[2]["policies"][1]["resources"]["com.cloudflare.api.account.a1"][
            "com.cloudflare.edge.worker.script.t4"] = "*"
        code, out = run(self.fake)
        self.assertEqual(code, 1)
        self.assertIn("Zone Settings Write", out)
        self.assertIn("another-site", out)
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn(f"{cs.DEPLOY_TOKEN}: left - run python3 tools/repo/cloudflare_tokens.py workers-deploy", out)

    def test_a_missing_or_expired_one_is_to_be_made(self):
        del self.fake.tokens[4]
        self.fake.tokens[0]["status"] = "expired"
        code, out = run(self.fake)
        self.assertEqual(code, 1)
        self.assertIn(f"{cs.TOKEN_PREFIX}readouts-review: does not exist", out)
        self.assertIn(f"{cs.TOKEN_PREFIX}settings-apply: does not exist", out)

    def test_an_apply_token_past_its_minutes_says_to_make_another(self):
        def refused(path, *args, **kwargs):
            raise cs.SettingsError(f"GET {path}: HTTP 403: Invalid access token", 403)
        err = io.StringIO()
        with mock.patch("sys.stderr", err):
            code, _ = run(refused, "apply")
        self.assertEqual(code, 1)
        self.assertIn("the settings-apply token lasts 10 minutes, from the address it was made from: in the "
                      "dashboard, give it a new TTL and Roll it", err.getvalue())

    def test_every_page_of_tokens_is_read(self):
        self.fake.tokens_page = 2
        self.assertEqual(run(self.fake)[0], 0)
        del self.fake.tokens[4]
        self.assertIn(f"{cs.TOKEN_PREFIX}readouts-review: does not exist", run(self.fake)[1])

    def test_another_of_the_projects_is_left_to_be_deleted(self):
        self.fake.tokens.append({"name": cs.TOKEN_PREFIX + "drift-detection-ci-read-only", "status": "active",
                                 "policies": []})
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("firmware-liberator-drift-detection-ci-read-only: left - delete it in the dashboard "
                      "once nothing uses it", out)

    def test_each_tool_reads_the_token_whose_job_it_is(self):
        def seen(main, *args):
            with mock.patch.object(cs, "api", side_effect=cs.SettingsError("stop")), redirect_stdout(io.StringIO()), \
                    mock.patch("sys.stderr", io.StringIO()):
                main(list(args))
            return cs.TOKEN_VARIABLE
        from tools.repo import api_health, readouts
        try:
            self.assertEqual(seen(cs.main, "apply"), "CLOUDFLARE_SETTINGS_APPLY_TOKEN")
            self.assertEqual(seen(cs.main, "ci"), "CLOUDFLARE_SETTINGS_CHECK_TOKEN")
            self.assertEqual(seen(cs.main, "deploy-token"), "CLOUDFLARE_WORKERS_DEPLOY_TOKEN")
            self.assertEqual(seen(api_health.main, "usage"), "CLOUDFLARE_SETTINGS_CHECK_TOKEN")
            self.assertEqual(seen(readouts.main, "review"), "CLOUDFLARE_READOUTS_REVIEW_TOKEN")
        finally:
            cs.TOKEN_VARIABLE = cs.TOKENS["settings-apply"]["variable"]

    def test_each_is_named_for_its_job_and_kept_under_its_variable(self):
        for key, spec in cs.TOKENS.items():
            self.assertIn(spec["kept"], ("github", "env", "worker", "asked"))
            # One kept on a Worker alone is kept under the Worker's name for it.
            self.assertEqual(spec.get("variable"), None if spec["kept"] == "worker" else "CLOUDFLARE_"
                             + key.removesuffix("-read-only").upper().replace("-", "_") + "_TOKEN", key)
            self.assertEqual(spec["kept"] == "worker", bool(spec.get("worker_secret")), key)
            self.assertEqual(key.endswith("-read-only"), all(
                name.endswith(" Read") for scope in ("zone", "account", "workers") for name in spec.get(scope, {})),
                key)


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
        self.fake.tokens = [listed("workers-deploy")]
        code, out = run(self.fake, "ci")
        self.assertEqual(code, 1)
        self.assertIn("can read the account's API tokens", out)
        self.assertEqual(self.fake.sent, [])



class DeployTokenReachTests(unittest.TestCase):
    """`deploy-token`, as the website workflow runs it with the deploy token:
    this zone and account, its three Workers, and no more."""

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
        self.assertIn("Remake the deploy token as TOKENS says: python3 tools/repo/cloudflare_tokens.py workers-deploy",
                      out)

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

    def test_the_website_workflow_runs_it_after_the_deploys(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/website.yml").read_text(encoding="utf-8")
        job = workflow.split("\n  deploy-token:\n", 1)[1]
        self.assertIn("needs: [deploy, deploy-api, deploy-admin]", job)
        self.assertIn("run: python3 tools/repo/cloudflare_settings.py deploy-token", job)
        self.assertIn("CLOUDFLARE_WORKERS_DEPLOY_TOKEN: ${{ secrets.CLOUDFLARE_WORKERS_DEPLOY_TOKEN }}", job)


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
            with mock.patch.object(cs, "api", fake), mock.patch.object(cs, "bound_database_id", lambda config=None: "u1"):
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
                      '{"/v1/wanted" "/v1/readout" "/v1/notify" "/v1/reports"})', expression)
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

    def test_a_kept_secret_is_asked_for_never_made_and_never_read_from_a_file(self):
        self.fake.secrets = [name for name in cs.API_SECRETS if name != "DEVICE_HASH_KEY_PERMANENT"]
        with mock.patch.dict("os.environ", {"API_DEVICE_HASH_KEY_PERMANENT": "not-taken-from-here"}):
            code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []), "with no terminal to ask at, nothing is made up")
        self.assertIn("secret DEVICE_HASH_KEY_PERMANENT: left - run apply in a terminal, which asks for its value, "
                      "kept in your password manager as DEVICE_HASH_KEY_PERMANENT", out)
        code, out = run(self.fake, "apply", answers=[""])
        self.assertIn("look for it in your password manager, under DEVICE_HASH_KEY_PERMANENT", out)
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("DEVICE_HASH_KEY_PERMANENT: left - not given", out)
        code, out = run(self.fake, "apply", answers=["\x1b[200~a-made-up-kept-value\x1b[201~"])
        self.assertEqual(code, 0, out)
        self.assertEqual([(body["name"], body["text"]) for _, _, body in self.fake.sent],
                         [("DEVICE_HASH_KEY_PERMANENT", "a-made-up-kept-value")])
        self.assertNotIn("a-made-up-kept-value", out)

    def test_the_alerts_address_is_the_one_mail_is_forwarded_to_never_shown(self):
        self.fake.secrets = [name for name in cs.API_SECRETS if name != "ALERTS_RECIPIENT_EMAIL"]
        code, out = run(self.fake, "apply")
        self.assertEqual(code, 0, out)
        [(method, path, body)] = [sent for sent in self.fake.sent if sent[1] == SECRETS]
        self.assertEqual((method, body["name"], body["text"]), ("PUT", "ALERTS_RECIPIENT_EMAIL", DESTINATION))
        self.assertNotIn(DESTINATION, out)

    def test_only_a_made_secret_is_rotated(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cs, "api", self.fake), redirect_stdout(out), mock.patch("sys.stderr", err):
            self.assertEqual(cs.main(["rotate", "DEVICE_HASH_KEY_PERMANENT"]), 2)
            self.assertEqual(self.fake.sent, [])
            self.assertIn("DEVICE_HASH_KEY_PERMANENT is kept, and must never change", err.getvalue())
            with mock.patch.object(cs, "asked_token", lambda: APPLY_VALUE):
                self.assertEqual(cs.main(["rotate", "SENDER_KEY"]), 0)
        [(_, _, body)] = self.fake.sent
        self.assertEqual(body["name"], "SENDER_KEY")
        self.assertNotIn(body["text"], out.getvalue())
        self.assertRegex(out.getvalue(), r"python3 tools/repo/readouts.py rotated \d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ",
                         "it says when, and how to find the keys this weakened")

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
        self.assertIn(f"{cs.API_WORKER} and {cs.ADMIN_WORKER} secrets: not checked", out)

    def test_the_worker_binds_the_figures_bucket_and_its_secrets_are_set_here(self):
        config = (Path(__file__).resolve().parents[2] / "website/api/wrangler.jsonc").read_text(encoding="utf-8")
        self.assertIn(f'"bucket_name": "{cs.STATS_BUCKET}"', config)
        self.assertNotIn("wrangler secret put", config, "set by cloudflare_settings.py apply, not by hand")
        for name in cs.API_SECRETS:
            self.assertIn(name, config)



class AdminAccessTests(unittest.TestCase):
    """Access in front of the admin view, and the admin Worker's settings."""

    def setUp(self):
        self.fake = FakeCloudflare()
        self.fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09"}]
        run(self.fake, "apply")
        self.fake.sent.clear()
        self.assertEqual(run(self.fake)[0], 0, "the fake starts with Access as apply leaves it")

    def fresh(self):
        self.fake.idps, self.fake.apps, self.fake.policies = [], [], {}

    def test_with_no_organisation_it_says_to_make_one_and_nothing_else(self):
        self.fake.organisation = None
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("Access: left - in the dashboard, Zero Trust: choose a team name", out)

    def test_a_token_not_allowed_access_says_so_and_the_rest_is_still_checked(self):
        self.fake.organisation = "forbidden"
        self.fake.r2_dev = True
        code, out = run(self.fake, "apply")
        self.assertEqual(code, 1)
        self.assertIn("Access: cannot be checked: this token is not allowed Access", out)
        self.assertIn("Access: left - remake the apply token, which TOKENS allows Access: "
                      "python3 tools/repo/cloudflare_tokens.py settings-apply", out)
        self.assertFalse(self.fake.r2_dev, "what it can set, it sets")

    def test_with_no_github_app_kept_it_says_how_to_make_one_with_its_callback(self):
        self.fresh()
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("https://team1.cloudflareaccess.com/cdn-cgi/access/callback", out)
        self.assertIn("then run apply in a terminal, which asks for its client id and a new secret", out)

    def test_apply_makes_the_sign_in_the_application_and_its_policy_never_showing_them(self):
        self.fresh()
        # Asked for, a paste's wrapping taken off; never kept.
        code, out = run(self.fake, "apply", answers=["\x1b[200~made-up-id\x1b[201~", "made-up-secret\n"])
        self.assertEqual(code, 1, "it says what to put in the admin wrangler.jsonc")
        for value in ("made-up-id", "made-up-secret", DESTINATION):
            self.assertNotIn(value, out)
        [idp] = self.fake.idps
        self.assertEqual(idp["config"], {"client_id": "made-up-id", "client_secret": "made-up-secret"})
        [app] = self.fake.apps
        self.assertEqual((app["domain"], app["allowed_idps"], app["auto_redirect_to_identity"]),
                         (cs.ADMIN_HOST, [idp["id"]], True))
        [policy] = self.fake.policies[app["id"]]
        self.assertEqual((policy["decision"], policy["include"], policy["require"]),
                         ("allow", [{"email": {"email": DESTINATION}}], [{"login_method": {"id": idp["id"]}}]))
        self.fake.admin_vars = {"ACCESS_TEAM": "team1", "ACCESS_AUD": app["aud"], "ACCOUNT_ID": "a1"}
        self.assertEqual(run(self.fake)[0], 0, "then nothing differs")

    def test_a_sign_in_not_given_is_left_and_the_rest_still_made(self):
        self.fresh()
        code, out = run(self.fake, "apply", answers=["", ""])
        self.assertEqual(code, 1)
        self.assertIn("Access GitHub sign-in: left - not given: run apply again", out)
        self.assertNotIn("Access GitHub sign-in: set", out)
        self.assertEqual(self.fake.idps, [])

    def test_an_admin_address_of_its_own_comes_before_the_mail_destination(self):
        with mock.patch.dict("os.environ", {"ACCESS_ADMIN_EMAIL": "admin@example.com"}):
            code, out = run(self.fake, "apply")
        self.assertNotIn("admin@example.com", out)
        [policy] = self.fake.policies["app1"]
        self.assertEqual(policy["include"], [{"email": {"email": "admin@example.com"}}])

    def test_a_policy_widened_by_hand_is_put_back_its_rules_never_printed(self):
        self.fake.policies["app1"][0]["include"] = [{"everyone": {}}]
        code, out = run(self.fake)
        self.assertEqual(code, 1)
        self.assertIn("lets in someone other than the maintainer", out)
        self.assertNotIn("everyone", out)
        run(self.fake, "apply")
        self.assertEqual(self.fake.policies["app1"][0]["include"], [{"email": {"email": DESTINATION}}])

    def test_a_second_policy_is_left_to_the_maintainer(self):
        self.fake.policies["app1"].append({"id": "p2", "decision": "bypass", "include": [{"everyone": {}}]})
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("are 2: there should be one", out)

    def test_another_sign_in_allowed_on_the_application_is_taken_off(self):
        self.fake.apps[0]["allowed_idps"] = ["g1", "otp"]
        code, out = run(self.fake, "apply")
        self.assertIn(f"Access application {cs.ADMIN_HOST}: differs in allowed_idps", out)
        self.assertEqual(self.fake.apps[0]["allowed_idps"], ["g1"])

    def test_the_admin_workers_vars_are_said_to_set_by_hand(self):
        self.fake.admin_vars = {"ACCESS_TEAM": "", "ACCESS_AUD": "", "ACCOUNT_ID": ""}
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("website/admin/wrangler.jsonc ACCESS_AUD: left - set it to 'aud1' and commit", out)

    def test_the_admin_workers_secret_is_a_token_kept_on_it_alone(self):
        self.fake.admin_secrets = []
        with mock.patch.dict("os.environ", {"CLOUDFLARE_ADMIN_DASHBOARD_TOKEN": "made-up-token"}):
            code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn(f"{cs.ADMIN_WORKER} secret ANALYTICS_TOKEN: left - make it: "
                      "python3 tools/repo/cloudflare_tokens.py admin-dashboard-read-only", out)

    def test_ci_asks_nothing_of_access(self):
        def no_access(path, *args, **kwargs):
            self.assertFalse(path.startswith(ACCESS) or path == ADMIN_SECRETS, path)
            return self.fake(path, *args, **kwargs)
        code, out = run(no_access, "ci")
        self.assertIn(f"Access for {cs.ADMIN_HOST}: not checked", out)

    def test_both_workers_bind_the_same_database(self):
        self.assertEqual(cs.bound_database_id(cs.ADMIN_WRANGLER), cs.bound_database_id())

    def test_the_website_workflow_deploys_the_admin_view_and_checks_it_is_closed(self):
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/website.yml").read_text(encoding="utf-8")
        job = workflow[workflow.index("  deploy-admin:"):workflow.index("  deploy-token:")]
        self.assertIn("npx wrangler deploy --config admin/wrangler.jsonc", job)
        self.assertIn("github.event_name != 'schedule'", job)
        self.assertIn("https://admin.firmwareliberator.com/", job)
        self.assertIn("needs: [deploy, deploy-api, deploy-admin]", workflow)
        self.assertIn(cs.ADMIN_WORKER, cs.DEPLOY_WORKERS)


class WebAnalyticsTests(unittest.TestCase):
    """Web Analytics: the zone's one site, the pages' tag naming it, not injected by Cloudflare."""

    def setUp(self):
        self.fake = FakeCloudflare()
        self.fake.addresses = [{"email": DESTINATION, "verified": "2026-11-09"}]
        run(self.fake, "apply")
        self.fake.sent.clear()

    def test_the_pages_carry_the_beacon_tag_once(self):
        page = cs.ANALYTICS_PAGE.read_text(encoding="utf-8")
        self.assertEqual(len(cs.ANALYTICS_BEACON.findall(page)), 1)
        self.assertEqual(page.count("static.cloudflareinsights.com/beacon.min.js"), 1)

    def test_automatic_setup_turned_on_by_hand_is_turned_off(self):
        self.fake.analytics[0]["auto_install"] = True
        code, out = run(self.fake, "apply")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.fake.sent, [("PUT", f"{ANALYTICS}/t1", {"auto_install": False, "zone_tag": "z1"})])
        self.assertEqual(run(self.fake)[0], 0)

    def test_none_is_made_and_its_token_left_to_put_in_the_page(self):
        self.fake.analytics = []
        code, out = run(self.fake, "apply")
        self.assertEqual(code, 1)
        self.assertEqual(self.fake.sent, [("POST", ANALYTICS, {"zone_tag": "z1", "auto_install": False})])
        self.assertIn(f"website/src/layouts/Page.astro: set the beacon tag's token to {'f' * 32}", out)

    def test_another_sites_token_in_the_page_is_left_to_the_maintainer(self):
        self.fake.analytics[0]["site_token"] = "e" * 32
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn(f"carries another site's token: Web Analytics' site for {cs.ZONE} is {'e' * 32}", out)

    def test_a_rule_or_a_second_site_is_left_to_the_dashboard(self):
        self.fake.analytics[0]["rules"].append({**ALL_MEASURED, "id": "ru2", "paths": ["/private*"],
                                                "inclusive": False})
        code, out = run(self.fake, "apply")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("Web Analytics' rules: narrow what is measured ([('*', ['/private*'])])", out)
        self.fake.analytics[0]["rules"] = [{**ALL_MEASURED, "is_paused": True}]
        self.assertIn("narrow what is measured", run(self.fake)[1])
        self.fake.analytics = [self.fake.analytics[0], {**self.fake.analytics[0], "site_tag": "t2"}]
        self.assertIn("has 2 sites for", run(self.fake)[1])

    def test_another_zones_site_is_not_this_ones(self):
        self.fake.analytics.append({"site_tag": "t9", "site_token": "9" * 32, "auto_install": True, "rules": [],
                                    "ruleset": {"zone_tag": "o1", "zone_name": "example.com"}})
        self.assertEqual(run(self.fake, "apply")[0], 0)
        self.assertEqual(self.fake.sent, [])

    def test_ci_never_asks_for_it(self):
        fake = self.fake

        def no_rum(path, *args, **kwargs):
            if "/rum/" in path:   # only the read CI's token must be refused, to show it is
                self.assertEqual((path, kwargs.get("ok_forbidden")), (f"{ANALYTICS}/list", True))
            return fake(path, *args, **kwargs)
        code, out = run(no_rum, "ci")
        self.assertIn("Web Analytics: not checked", out)
        self.fake.readable.add(f"{ANALYTICS}/list")
        self.assertIn("can read the account's settings (Web Analytics sites): it should be refused",
                      run(self.fake, "ci")[1])


class UnwrittenTests(unittest.TestCase):
    """Nothing of the project's is there that nothing here says should be: the
    account holds other projects' too, and Cloudflare's own rulesets."""

    def setUp(self):
        self.fake = FakeCloudflare()

    def said(self, *args):
        return run(self.fake, *args)[1]

    def test_what_is_written_and_what_is_not_the_projects_is_no_difference(self):
        self.fake.buckets |= {cs.READOUTS_BUCKET, cs.STATS_BUCKET, "another-projects-bucket"}
        out = self.said()
        for what in ("zone ruleset", "Worker route", "R2 bucket", "Worker another-site", "custom domain"):
            self.assertNotIn(what, out)

    def test_a_ruleset_in_a_phase_not_kept_here_is_deleted(self):
        self.fake.other_rulesets = [{"id": "x1", "kind": "zone", "phase": "http_request_transform", "name": "x"}]
        self.assertIn("zone ruleset http_request_transform: is not written here", self.said())
        self.said("apply")
        self.assertIn(("DELETE", f"{ZONE}/rulesets/x1", None), self.fake.sent)

    def test_a_worker_route_is_deleted(self):
        self.fake.routes = [{"id": "rt1", "pattern": f"{cs.ZONE}/admin*", "script": "another-site"}]
        self.assertIn(f"Worker route {cs.ZONE}/admin*: is not written here", self.said())
        self.said("apply")
        self.assertIn(("DELETE", f"{ZONE}/workers/routes/rt1", None), self.fake.sent)

    def test_a_custom_domain_no_wrangler_config_names_is_deleted(self):
        self.assertIn((f"api.{cs.ZONE}", cs.API_WORKER), cs.custom_domains())
        self.fake.custom_domains.append({"id": "cd9", "hostname": f"old.{cs.ZONE}", "service": cs.API_WORKER})
        self.fake.custom_domains[0]["service"] = "another-site"
        out = self.said()
        self.assertIn(f"Worker custom domain old.{cs.ZONE}: points at {cs.API_WORKER}, which no wrangler.jsonc says",
                      out)
        self.assertIn("points at another-site", out)
        self.said("apply")
        self.assertIn(("DELETE", "/accounts/a1/workers/domains/cd9", None), self.fake.sent)

    def test_a_projects_worker_or_bucket_not_written_here_is_left_to_be_deleted_by_hand(self):
        self.fake.workers.append("firmware-liberator-old")
        self.fake.buckets |= {"firmware-liberator-old"}
        out = self.said()
        self.assertIn("Worker firmware-liberator-old: is not written here", out)
        self.assertIn("R2 bucket firmware-liberator-old: is not written here", out)
        _, applied = run(self.fake, "apply")
        self.assertIn("Worker firmware-liberator-old: left - delete it", applied)
        self.assertIn("R2 bucket firmware-liberator-old: left - empty and delete it", applied)
        self.assertFalse([sent for sent in self.fake.sent if "firmware-liberator-old" in sent[1]])

    def test_a_check_the_token_is_refused_is_a_difference_never_a_pass(self):
        self.fake.refused = {f"{ZONE}/rulesets", f"{ZONE}/workers/routes", BUCKETS}
        code, out = run(self.fake, "ci")
        self.assertEqual(code, 1)
        self.assertIn("zone rulesets: cannot be checked: this token is refused them", out)
        self.assertIn("Worker routes: cannot be checked", out)
        self.assertIn("R2 buckets: cannot be checked", out)
        _, applied = run(self.fake, "apply")
        self.assertIn("Worker routes: left - give it Workers Routes Read, or Write (TOKENS), and remake it: "
                      "python3 tools/repo/cloudflare_tokens.py settings-apply", applied)

    def test_each_permission_asked_for_is_in_the_tokens_lists(self):
        for permission in ("Zone Transform Rules", "Workers Routes", "Workers R2 Storage"):
            for key in ("settings-check-read-only", "settings-apply"):
                spec = cs.TOKENS[key]
                self.assertTrue(any(name.startswith(permission) for scope in ("zone", "account")
                                    for name in spec.get(scope, {})), (key, permission))

    def test_a_change_apply_is_refused_is_left_and_the_rest_still_made(self):
        self.fake.other_rulesets = [{"id": "x1", "kind": "zone", "phase": "http_request_origin", "name": "x"}]
        self.fake.refused = {f"{ZONE}/rulesets/x1"}
        code, applied = run(self.fake, "apply")
        self.assertEqual(code, 1)
        self.assertIn("zone ruleset http_request_origin: left - this token is refused it", applied)
        # What came after it in the list was still made.
        refused_at = applied.index("zone ruleset http_request_origin: left")
        self.assertGreater(applied.index(f"{cs.READOUTS_BUCKET}: set"), refused_at)
        self.assertIn(("PATCH", f"{ZONE}/dnssec", {"status": "active"}), self.fake.sent)

    def test_ci_leaves_the_workers_to_the_hand_check(self):
        self.fake.workers = []
        out = self.said("ci")
        self.assertIn("Workers and their custom domains: not checked", out)


class AskedTokenTests(unittest.TestCase):
    """The apply token, asked for each run and taken only as TOKENS has it."""

    NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.timezone.utc)

    def setUp(self):
        self.token = {"id": "apply1", "name": cs.TOKEN_PREFIX + "settings-apply", "status": "active",
                      "expires_on": "2026-11-10T12:08:00Z", "condition": {"request_ip": {"in": ["192.0.2.50/32"]}}}
        self.refuse = False

    def api(self, path, method="GET", body=None, **kwargs):
        if self.refuse or cs.token() != APPLY_VALUE:
            raise cs.SettingsError(f"{method} {path}: HTTP 403: Invalid access token", 403)
        answers = {f"/zones?name={cs.ZONE}": [{"id": "z1", "account": {"id": "a1"}}],
                   "/accounts/a1/tokens/verify": {"id": "apply1", "status": "active"},
                   "/accounts/a1/tokens/apply1": self.token}
        return copy.deepcopy(answers[path])

    def ask(self, pasted=APPLY_VALUE, tty=True):
        stdin, err = io.StringIO(), io.StringIO()
        stdin.isatty = lambda: tty
        with mock.patch.object(cs, "api", self.api), mock.patch("sys.stdin", stdin), mock.patch("sys.stderr", err), \
                mock.patch.object(cs.getpass, "getpass", return_value=pasted), \
                mock.patch.object(cs, "own_address", lambda: "192.0.2.50/32"):
            return cs.asked_token(self.NOW), err.getvalue()

    def test_one_rolled_for_this_run_from_here_is_taken_and_never_written_down(self):
        value, said = self.ask(f"\x1b[200~{APPLY_VALUE}\x1b[201~")
        self.assertEqual(value, APPLY_VALUE)
        self.assertIn("give it a TTL ending within 10 minutes and Roll it", said)
        self.assertNotIn(APPLY_VALUE, said)

    def test_one_otherwise_is_refused_saying_why(self):
        for change, why in (({"name": "another"}, "the token given is 'another'"),
                            ({"expires_on": None}, "expires never: give it a TTL ending within 10 minutes"),
                            ({"expires_on": "2026-11-10T12:10:01Z"}, "give it a TTL ending within 10 minutes"),
                            ({"condition": None}, "usable from more than this computer's address"),
                            ({"condition": {"request_ip": {"in": ["192.0.2.0/24"]}}}, "more than this computer's")):
            with self.subTest(change=change):
                self.setUp()
                self.token.update(change)
                value, said = self.ask()
                self.assertIsNone(value)
                self.assertIn(why, said)

    def test_one_cloudflare_will_not_take_is_said_to_check_its_ttl(self):
        self.refuse = True
        value, said = self.ask()
        self.assertIsNone(value)
        self.assertIn("Cloudflare does not take that token", said)
        self.assertIn("check its TTL", said)

    def test_with_no_terminal_none_is_asked_for(self):
        value, said = self.ask(tty=False)
        self.assertIsNone(value)
        self.assertIn("run this in a terminal", said)

    def test_main_asks_for_it_and_uses_it_for_the_run_alone(self):
        stdin = io.StringIO()
        with mock.patch.object(cs, "asked_token", lambda: None), mock.patch("sys.stdin", stdin):
            self.assertEqual(cs.main(["apply"]), 1, "no token given: nothing done")


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

    def test_the_daily_build_follows_the_nightly_job_and_never_redeploys_the_api(self):
        root = Path(__file__).resolve().parents[2]
        workflow = (root / ".github/workflows/website.yml").read_text(encoding="utf-8")
        self.assertIn('- cron: "30 4 * * *"', workflow)
        self.assertIn('"crons": ["17 3 * * *"', (root / "website/api/wrangler.jsonc").read_text(encoding="utf-8"))
        job = workflow[workflow.index("\n  deploy-api:"):workflow.index("\n  deploy-token:")]
        self.assertIn("github.event_name != 'schedule'", job.split("runs-on:", 1)[0], "a stopped API stays stopped")
        self.assertIn("REQUIRE_STATS: ${{ github.event_name == 'schedule' && '1' || '' }}", workflow)

    def test_the_switch_is_the_workers_own(self):
        root = Path(__file__).resolve().parents[2]
        self.assertIn('"INGEST": "on"', (root / "website/api/wrangler.jsonc").read_text(encoding="utf-8"))
        self.assertIn('if (env.INGEST !== "on") {', (root / "website/api/index.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
