import copy
import io
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


class FakeCloudflare:
    """Cloudflare's API for one zone, as tools/repo/cloudflare_settings.py sees it."""

    def __init__(self):
        self.settings = {"always_use_https": "off", "min_tls_version": "1.0"}
        self.rulesets = {}  # entry point path: its rules, as Cloudflare keeps them
        self.buckets = set()
        self.lifecycle = None
        self.tokens = None   # the account's API tokens; None: this token may not read them, as CI's
        self.sent = []

    @property
    def redirects(self):
        return self.rulesets.get(ENTRYPOINT)

    def __call__(self, path, method="GET", body=None, ok_missing=False, ok_forbidden=False):
        if method != "GET":
            self.sent.append((method, path, body))
            if path == BUCKETS:
                self.buckets.add(body["name"])
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
        if path == "/accounts/a1/tokens":
            assert ok_forbidden
            return copy.deepcopy(self.tokens)
        if path == "/accounts/a1/workers/scripts":
            return [{"id": "firmware-liberator", "tag": "t1"}, {"id": "firmware-liberator-api", "tag": "t2"},
                    {"id": "another-site", "tag": "t3"}]
        if path.startswith(f"{BUCKETS}/") and path.endswith("/lifecycle"):
            assert ok_missing
            return self.lifecycle
        if path.startswith(f"{BUCKETS}/"):
            assert ok_missing
            name = path.rsplit("/", 1)[1]
            return {"name": name} if name in self.buckets else None
        if path.startswith(f"{ZONE}/settings/"):
            return {"id": path.rsplit("/", 1)[1], "value": self.settings[path.rsplit("/", 1)[1]]}
        if "/rulesets/phases/" in path:
            assert ok_missing
            return {"rules": self.rulesets[path]} if path in self.rulesets else None
        raise AssertionError(f"unexpected {method} {path}")


def run(fake, *args):
    out = io.StringIO()
    with mock.patch.object(cs, "api", fake), redirect_stdout(out):
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

    def test_apply_sets_them_and_then_sends_nothing(self):
        fake = FakeCloudflare()
        self.assertEqual(run(fake, "apply")[0], 0)
        self.assertEqual(fake.settings, cs.SETTINGS)
        self.assertEqual([cs.comparable(rule) for rule in fake.redirects], cs.REDIRECTS)
        self.assertEqual(fake.buckets, {cs.READOUTS_BUCKET})
        self.assertEqual([cs.comparable(rule, cs.RATE_LIMITS[0]) for rule in fake.rulesets[RATE_LIMITS]],
                         cs.RATE_LIMITS)
        self.assertEqual(fake.lifecycle, cs.READOUTS_LIFECYCLE)
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

    def test_a_lifecycle_rule_changed_by_hand_is_put_back(self):
        fake = FakeCloudflare()
        run(fake, "apply")
        fake.lifecycle["rules"][0]["deleteObjectsTransition"]["condition"]["maxAge"] = 10 ** 9
        fake.sent.clear()
        run(fake, "apply")
        self.assertEqual([path for _, path, _ in fake.sent], [f"{BUCKETS}/{cs.READOUTS_BUCKET}/lifecycle"])

    def test_the_readouts_go_to_the_bucket_it_makes(self):
        config = (Path(__file__).resolve().parents[2] / "website/api/wrangler.jsonc").read_text(encoding="utf-8")
        self.assertIn(f'"bucket_name": "{cs.READOUTS_BUCKET}"', config)

    def test_the_token_comes_from_the_environment_or_env(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            with mock.patch.object(cs.credentials, "ENV_FILE", env_file), \
                    mock.patch.dict("os.environ", {"CLOUDFLARE_API_TOKEN": ""}):
                with self.assertRaisesRegex(cs.SettingsError, "no CLOUDFLARE_API_TOKEN"):
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

    def test_the_workflow_only_checks(self):
        # Settings are applied by hand; the workflow's token can only read them.
        workflow = (Path(__file__).resolve().parents[2] / ".github/workflows/cloudflare-settings.yml").read_text(
            encoding="utf-8")
        self.assertNotIn(" apply", workflow.split("\nname:", 1)[1])
        self.assertIn("run: python3 tools/repo/cloudflare_settings.py\n", workflow)
        self.assertIn("secrets.CLOUDFLARE_READ_TOKEN", workflow)


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
        code = ("from tests.repo.test_cloudflare_settings import FakeCloudflare, deploy_token, run\n"
                "fake = FakeCloudflare(); run(fake, 'apply'); fake.tokens = [deploy_token()]\n"
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


if __name__ == "__main__":
    unittest.main()
