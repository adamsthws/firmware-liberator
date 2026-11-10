import datetime as dt
import io
import stat
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from tools.repo import cloudflare_settings as cs
from tools.repo import cloudflare_tokens as ct

NOW = dt.datetime(2026, 11, 10, 12, 0, tzinfo=dt.timezone.utc)
TOKENS = "/accounts/a1/tokens"
# A made-up rotate token's value, shaped as one.
ROTATE = "made-up-rotate-token-0123456789abcdefghij"
WORKERS = ["firmware-liberator", "firmware-liberator-api", "firmware-liberator-admin", "another-site"]


class FakeCloudflare:
    """Cloudflare's API as cloudflare_tokens.py sees it, run with the rotate token."""

    def __init__(self):
        self.own = {"id": "own", "name": cs.TOKEN_PREFIX + ct.ROTATE_KEY, "status": "active",
                    "issued_on": "2026-11-10T11:58:00Z", "expires_on": "2026-11-10T12:08:00Z",
                    "policies": [{"effect": "allow", "permission_groups": [{"name": "Account API Tokens Write"}],
                                  "resources": {"com.cloudflare.api.account.a1": "*"}}],
                    "condition": {"request_ip": {"in": ["192.0.2.50/32"], "not_in": []}}}
        # One made before, to be remade; the rest not there yet.
        self.tokens = [self.own, {"id": "old-check", "name": cs.TOKEN_PREFIX + "settings-check-read-only",
                                  "status": "active", "policies": []}]
        self.workers = list(WORKERS)
        self.secrets = {}   # (worker, name): value
        self.sent = []
        self.values = iter(f"made-up-value-{n}" for n in range(100))
        self.fail = set()
        self.helper_value = None
        self.slow = 0   # how many requests a new helper is refused, as Cloudflare can for a moment

    def groups(self):
        names = {name for spec in cs.TOKENS.values() for scope in ("zone", "account", "workers")
                 for name in spec.get(scope, {})}
        kinds = {"zone": "com.cloudflare.api.account.zone", "account": "com.cloudflare.api.account",
                 "workers": "com.cloudflare.edge.worker.script"}
        found = []
        for name in sorted(names):
            scopes = sorted({kinds[scope] for spec in cs.TOKENS.values() for scope in kinds
                             if name in spec.get(scope, {})})
            # Cloudflare lists some names twice, a group for each scope.
            found += [{"id": f"{name}@{kind}", "name": name, "scopes": [kind]} for kind in scopes]
        return found + [{"id": "zone-only", "name": "Access: Apps and Policies Write",
                         "scopes": ["com.cloudflare.api.account.zone"]},
                        {"id": "zone-read", "name": "Zone Read", "scopes": ["com.cloudflare.api.account.zone"]},
                        {"id": "scripts-write", "name": "Workers Scripts Write",
                         "scopes": ["com.cloudflare.api.account"]}]

    def __call__(self, path, method="GET", body=None, ok_missing=False, ok_forbidden=False, whole=False):
        # The rotate token is allowed the account's tokens alone; the rest needs the helper.
        used = cs.token()
        helper = path.startswith(("/zones", "/accounts/a1/workers/"))
        if used != (self.helper_value if helper else ROTATE):
            raise cs.SettingsError(f"{method} {path}: HTTP 403: Invalid access token", 403)
        if path in self.fail:
            raise cs.SettingsError(f"{method} {path}: HTTP 500: made-up failure", 500)
        if method != "GET":
            self.sent.append((method, path, body))
        if path == "/accounts?per_page=50":
            return [{"id": "a1"}]
        if path == f"/zones?name={cs.ZONE}":
            if self.slow:
                self.slow -= 1
                raise cs.SettingsError(f"GET {path}: HTTP 401: not yet", 401)
            return [{"id": "z1", "name": cs.ZONE, "account": {"id": "a1"}}]
        if path == f"{TOKENS}/verify":
            return {"id": "own", "status": "active"}
        if path == f"{TOKENS}/permission_groups":
            return self.groups()
        if path == "/accounts/a1/workers/scripts":
            return [{"id": name, "tag": f"t{n + 1}"} for n, name in enumerate(self.workers)]
        if path.startswith(f"{TOKENS}?per_page=50&page="):
            return {"result": [dict(token) for token in self.tokens], "result_info": {"total_pages": 1}}
        if path == TOKENS and method == "POST":
            self.tokens.append({"id": f"id-{body['name']}", "status": "active", **body})
            value = next(self.values)
            if body["name"].endswith(ct.HELPER_KEY):
                self.helper_value = value
            return {"id": f"id-{body['name']}", "value": value}
        if path.startswith(f"{TOKENS}/"):
            token_id = path[len(TOKENS) + 1:].removesuffix("/value")
            token = next(token for token in self.tokens if token["id"] == token_id)
            if method == "DELETE":
                self.tokens.remove(token)
                return None
            if method == "PUT" and path.endswith("/value"):
                return next(self.values)
            if method == "PUT":
                token.update(body)
                return token
            return dict(token)
        if path.endswith("/secrets") and method == "PUT":
            self.secrets[(path.split("/")[5], body["name"])] = body["text"]
            return None
        raise AssertionError(f"unexpected {method} {path}")


class TokensTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeCloudflare()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.env = Path(self.directory.name) / ".env.infra"
        self.env.write_text("# a comment, kept\nCLOUDFLARE_SETTINGS_APPLY_TOKEN=an-old-one\n"
                            "ACCESS_ADMIN_EMAIL=owner@example.org\n", encoding="utf-8")
        self.github = {}
        self.addCleanup(setattr, cs, "TOKEN_VARIABLE", cs.TOKEN_VARIABLE)

    def run_it(self, *keys, signed_in=True, pasted=ROTATE):
        """main, as run in a terminal where `pasted` is given at the prompt."""
        out, err = io.StringIO(), io.StringIO()
        auth = mock.Mock(returncode=0 if signed_in else 1)
        tty = io.StringIO()
        tty.isatty = lambda: True
        with mock.patch.object(cs, "api", self.fake), mock.patch.object(cs, "ENV_FILE", self.env), \
                mock.patch.object(ct, "own_address", lambda: "192.0.2.50/32"), \
                mock.patch.object(ct.time, "sleep", lambda seconds: None), \
                mock.patch("sys.stdin", tty), mock.patch.object(cs.getpass, "getpass", return_value=pasted), \
                mock.patch.object(ct.github_settings, "set_secret", self.github.__setitem__), \
                mock.patch.object(ct.subprocess, "run", return_value=auth), \
                redirect_stdout(out), mock.patch("sys.stderr", err):
            code = ct.main(list(keys), now=NOW)
        return code, out.getvalue() + err.getvalue()

    def test_makes_each_puts_each_value_where_it_is_kept_and_deletes_itself(self):
        code, out = self.run_it()
        self.assertEqual(code, 0, out)
        names = {token["name"] for token in self.fake.tokens}
        self.assertEqual(names, {cs.TOKEN_PREFIX + key for key in cs.TOKENS})
        self.assertEqual(set(self.github), {"CLOUDFLARE_SETTINGS_CHECK_TOKEN", "CLOUDFLARE_WORKERS_DEPLOY_TOKEN"})
        env = cs.credentials.parse("\n".join(line for line in self.env.read_text(encoding="utf-8").splitlines()
                                             if "=" in line))
        for key, spec in cs.TOKENS.items():
            if spec["kept"] == "env":
                self.assertTrue(env[spec["variable"]].startswith("made-up-value-"), key)
        # The admin view's is on its Worker alone.
        self.assertTrue(self.fake.secrets[(cs.ADMIN_WORKER, "ANALYTICS_TOKEN")].startswith("made-up-value-"))
        self.assertNotIn(self.fake.secrets[(cs.ADMIN_WORKER, "ANALYTICS_TOKEN")], self.env.read_text(encoding="utf-8"))
        self.assertIn(("DELETE", f"{TOKENS}/own", None), self.fake.sent)
        self.assertIn(("DELETE", f"{TOKENS}/id-{cs.TOKEN_PREFIX}{ct.HELPER_KEY}", None), self.fake.sent)
        self.assertNotIn("made-up-value", out)
        self.assertNotIn("an-old-one", self.env.read_text(encoding="utf-8"))

    def test_the_helper_is_deleted_once_its_last_use_is_done(self):
        self.run_it()
        paths = [(method, path) for method, path, _ in self.fake.sent]
        helper = paths.index(("DELETE", f"{TOKENS}/id-{cs.TOKEN_PREFIX}{ct.HELPER_KEY}"))
        secret = paths.index(("PUT", f"/accounts/a1/workers/scripts/{cs.ADMIN_WORKER}/secrets"))
        first_other = min(n for n, (method, path) in enumerate(paths)
                          if method == "POST" and n > 0 and self.fake.sent[n][2]["name"]
                          not in (cs.TOKEN_PREFIX + ct.HELPER_KEY, cs.TOKEN_PREFIX + "admin-dashboard-read-only"))
        self.assertLess(secret, helper)
        self.assertLess(helper, first_other)

    def test_the_helper_is_for_a_minute_from_this_address_alone(self):
        self.fake.slow = 2
        code, out = self.run_it("readouts-review")
        self.assertEqual(code, 0, out)
        made = next(body for method, path, body in self.fake.sent
                    if method == "POST" and body["name"] == cs.TOKEN_PREFIX + ct.HELPER_KEY)
        self.assertEqual(made["expires_on"], "2026-11-10T12:01:00Z")
        self.assertEqual(made["condition"], {"request_ip": {"in": ["192.0.2.50/32"]}})
        self.assertEqual([group for policy in made["policies"] for group in policy["permission_groups"]],
                         [{"id": "zone-read"}, {"id": "scripts-write"}])
        self.assertNotIn("helper", "".join(f"{n}" for n in self.env.read_text(encoding="utf-8").splitlines()))

    def test_its_address_is_the_one_cloudflare_sees_and_that_alone(self):
        for trace, network in (("ip=192.0.2.50\n", "192.0.2.50/32"),
                               ("ip=2001:db8::1:2:3\nloc=GB\n", "2001:db8::1:2:3/128")):
            answer = mock.MagicMock()
            answer.__enter__.return_value.read.return_value = trace.encode()
            with mock.patch.object(cs.urllib.request, "urlopen", return_value=answer):
                self.assertEqual(ct.own_address(), network)

    def test_one_there_is_remade_in_place_its_value_rolled(self):
        self.run_it("settings-check-read-only")
        put, roll = [sent for sent in self.fake.sent if sent[1].startswith(f"{TOKENS}/old-check")]
        self.assertEqual(put[0], "PUT")
        self.assertEqual(put[2]["status"], "active")
        self.assertEqual(roll[:2], ("PUT", f"{TOKENS}/old-check/value"))
        self.assertFalse([sent for sent in self.fake.sent if sent[:2] == ("POST", TOKENS)
                          and sent[2]["name"] == cs.TOKEN_PREFIX + "settings-check-read-only"])

    def test_each_permission_goes_by_its_id_for_the_scope_it_is_given_on(self):
        made = ct.body("settings-apply", "a1", "z1", {}, {(g["name"], g["scopes"][0]): g["id"]
                                                          for g in self.fake.groups()}, NOW, "192.0.2.50/32")
        # Lasting minutes, from this computer alone: a change is applied as soon as it is made.
        self.assertEqual(made["expires_on"], "2026-11-10T12:10:00Z")
        self.assertEqual(made["condition"], {"request_ip": {"in": ["192.0.2.50/32"]}})
        account = next(policy for policy in made["policies"]
                       if "com.cloudflare.api.account.a1" in policy["resources"])
        self.assertIn({"id": "Access: Apps and Policies Write@com.cloudflare.api.account"},
                      account["permission_groups"])
        self.assertNotIn({"id": "zone-only"}, account["permission_groups"])
        deploy = ct.body("workers-deploy", "a1", "z1", {name: f"t{n}" for n, name in enumerate(WORKERS)},
                         {(g["name"], g["scopes"][0]): g["id"] for g in self.fake.groups()}, NOW, "192.0.2.50/32")
        self.assertNotIn("expires_on", deploy)
        self.assertNotIn("condition", deploy)
        workers = next(policy for policy in deploy["policies"] if policy["permission_groups"]
                       == [{"id": "Individual Workers Editor@com.cloudflare.edge.worker.script"}])
        self.assertEqual(workers["resources"], {"com.cloudflare.api.account.a1": {
            "com.cloudflare.edge.worker.script.t0": "*", "com.cloudflare.edge.worker.script.t1": "*",
            "com.cloudflare.edge.worker.script.t2": "*"}})

    def test_what_it_makes_is_what_apply_then_finds_as_written(self):
        self.run_it()
        workers = {f"com.cloudflare.edge.worker.script.t{n + 1}": name for n, name in enumerate(WORKERS)}
        ids = {g["id"]: g["name"] for g in self.fake.groups()}
        for token in self.fake.tokens:
            key = token["name"].removeprefix(cs.TOKEN_PREFIX)
            listed = {**token, "policies": [{**policy, "permission_groups": [
                {"name": ids[group["id"]]} for group in policy["permission_groups"]]}
                for policy in token["policies"]]}
            self.assertEqual(cs.live_policies(listed, "z1", workers), cs.written_policies(cs.TOKENS[key]), key)

    def test_it_refuses_another_token_changing_nothing_not_even_deleting_it(self):
        self.fake.own["name"] = "Another-Sites-Page-Cache"
        self.fake.own["expires_on"] = None   # however it was made: it is someone else's
        code, out = self.run_it()
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("run this with firmware-liberator-tokens-rotate alone", out)

    def assert_deleted_as_wrongly_made(self, why):
        code, out = self.run_it()
        self.assertEqual((code, self.fake.sent), (1, [("DELETE", f"{TOKENS}/own", None)]))
        self.assertIn(why, out)
        self.assertIn("It is deleted: make another as this says.", out)

    def test_one_that_outlives_its_job_is_deleted(self):
        for expires in (None, "2026-11-10T12:10:01Z", "2026-11-12T12:00:00Z"):
            with self.subTest(expires=expires):
                self.setUp()
                self.fake.own["expires_on"] = expires
                self.assert_deleted_as_wrongly_made(f"not within {ct.ROTATE_MINUTES} minutes")

    def test_one_made_more_than_minutes_ago_is_deleted(self):
        for issued in (None, "2026-11-10T11:49:59Z"):
            with self.subTest(issued=issued):
                self.setUp()
                self.fake.own["issued_on"] = issued
                self.assert_deleted_as_wrongly_made(f"not in the last {ct.ROTATE_MINUTES} minutes")

    def test_one_allowed_more_is_deleted(self):
        for extra in ({"effect": "allow", "permission_groups": [{"name": "DNS Write"}],
                       "resources": {"com.cloudflare.api.account.zone.z1": "*"}},
                      {"effect": "allow", "permission_groups": [{"name": "Account API Tokens Write"}],
                       "resources": {"com.cloudflare.api.account.a2": "*"}}):
            with self.subTest(extra=extra):
                self.setUp()
                self.fake.own["policies"].append(extra)
                self.assert_deleted_as_wrongly_made("it is allowed more than Account - Account API Tokens: Edit")

    def test_one_usable_from_more_than_this_address_is_deleted(self):
        for allowed in ([], ["192.0.2.0/24"], ["192.0.2.50/32", "198.51.100.7/32"]):
            with self.subTest(allowed=allowed):
                self.setUp()
                self.fake.own["condition"] = {"request_ip": {"in": allowed}} if allowed else None
                self.assert_deleted_as_wrongly_made("not this computer's alone (which Cloudflare sees as 192.0.2.50/32)")
        self.setUp()
        self.fake.own["condition"] = {"request_ip": {"in": ["192.0.2.50"]}}   # an address, as Cloudflare may keep it
        self.assertEqual(self.run_it("readouts-review")[0], 0)

    def test_its_token_is_asked_for_a_paste_unwrapped_and_never_written_down(self):
        before = self.env.read_text(encoding="utf-8")
        code, out = self.run_it("readouts-review", pasted=f"\x1b[200~{ROTATE}\x1b[201~\n")
        self.assertEqual(code, 0, out)
        self.assertIn(f"name: {cs.TOKEN_PREFIX}{ct.ROTATE_KEY}", out)
        self.assertNotIn(ROTATE, self.env.read_text(encoding="utf-8"))
        self.assertEqual(self.env.read_text(encoding="utf-8").replace(
            next(line for line in self.env.read_text(encoding="utf-8").splitlines()
                 if line.startswith("CLOUDFLARE_READOUTS_REVIEW_TOKEN=")) + "\n", ""), before)

    def test_from_the_environment_or_env_infra_it_is_not_taken(self):
        self.env.write_text(f"CLOUDFLARE_TOKENS_ROTATE_TOKEN={ROTATE}\n", encoding="utf-8")
        with mock.patch.dict("os.environ", {"CLOUDFLARE_TOKENS_ROTATE_TOKEN": ROTATE}):
            code, out = self.run_it(pasted="")
        self.assertEqual((code, self.fake.sent), (1, []))

    def test_what_is_no_tokens_value_is_said_never_shown_and_never_sent(self):
        for pasted, said in (("0123456789abcdef0123456789abcdef", "32 hex digits is a token's id, not its value"),
                             (f"{ROTATE} {ROTATE}", "with spaces"), (f"{ROTATE}\x07", "with control characters")):
            with self.subTest(said=said):
                code, out = self.run_it(pasted=pasted)
                self.assertEqual((code, self.fake.sent), (1, []))
                self.assertIn("that is not a token's value", out)
                self.assertIn(said, out)
                self.assertNotIn(pasted.strip(), out)

    def test_a_token_cloudflare_will_not_take_is_said_to_check_its_dates(self):
        code, out = self.run_it(pasted="made-up-but-token-shaped-value-0123456789")
        self.assertEqual((code, self.fake.sent), (1, []))
        self.assertIn("Cloudflare does not take that token", out)
        self.assertIn("check its TTL in the dashboard - start and end dates both today", out)
        self.assertNotIn("made-up-but-token-shaped", out)

    def test_a_step_that_fails_still_deletes_it(self):
        self.fake.fail = {f"{TOKENS}/old-check/value"}
        code, out = self.run_it()
        self.assertEqual(code, 1)
        self.assertIn("made-up failure", out)
        self.assertIn(("DELETE", f"{TOKENS}/own", None), self.fake.sent)

    def test_a_worker_not_there_yet_is_said_and_it_still_deletes_itself(self):
        self.fake.workers.remove("firmware-liberator-admin")
        code, out = self.run_it("workers-deploy")
        self.assertEqual(code, 1)
        self.assertIn("no Worker firmware-liberator-admin yet", out)
        self.assertIn(("DELETE", f"{TOKENS}/own", None), self.fake.sent)

    def test_without_gh_signed_in_no_github_token_is_remade(self):
        code, out = self.run_it("settings-check-read-only", signed_in=False)
        self.assertEqual(code, 1)
        self.assertIn("gh is not signed in", out)
        self.assertFalse([sent for sent in self.fake.sent if "old-check" in sent[1]])

    def test_another_of_the_projects_is_named_to_delete_by_hand(self):
        self.fake.tokens.append({"id": "x", "name": cs.TOKEN_PREFIX + "drift-detection-ci-read-only",
                                 "status": "active"})
        code, out = self.run_it("readouts-review")
        self.assertEqual(code, 1)
        self.assertIn("firmware-liberator-drift-detection-ci-read-only: left - it is not written in TOKENS", out)
        self.assertNotIn(("DELETE", f"{TOKENS}/x", None), self.fake.sent)

    def test_it_says_how_to_make_its_token_from_what_is_written(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(cs, "api", self.fake), redirect_stdout(out), mock.patch("sys.stderr", err), \
                mock.patch("sys.stdin", io.StringIO()):   # no terminal: nothing to ask at
            self.assertEqual(ct.main([], now=NOW), 1)
        said = err.getvalue()
        self.assertIn("run this in a terminal", said)
        self.assertIn(f"name: {cs.TOKEN_PREFIX}{ct.ROTATE_KEY}", said)
        for shown, _ in ct.ROTATE_PERMISSIONS.values():
            self.assertIn(shown, said)
        self.assertEqual(self.fake.sent, [])

    def test_an_unknown_key_changes_nothing(self):
        self.assertEqual(self.run_it("settings")[0], 2)
        self.assertEqual(self.fake.sent, [])

    def test_env_infra_keeps_its_other_lines_and_its_owner_alone_reads_it(self):
        with mock.patch.object(cs, "ENV_FILE", self.env):
            ct.write_env("CLOUDFLARE_SETTINGS_APPLY_TOKEN", "new")
            ct.write_env("CLOUDFLARE_READOUTS_REVIEW_TOKEN", "added")
        self.assertEqual(self.env.read_text(encoding="utf-8").splitlines(), [
            "# a comment, kept", "CLOUDFLARE_SETTINGS_APPLY_TOKEN=new", "ACCESS_ADMIN_EMAIL=owner@example.org",
            "CLOUDFLARE_READOUTS_REVIEW_TOKEN=added"])
        self.assertEqual(stat.S_IMODE(self.env.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
