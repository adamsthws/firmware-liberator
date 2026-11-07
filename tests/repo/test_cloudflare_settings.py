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
        self.sent = []

    @property
    def redirects(self):
        return self.rulesets.get(ENTRYPOINT)

    def __call__(self, path, method="GET", body=None, ok_missing=False):
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


if __name__ == "__main__":
    unittest.main()
