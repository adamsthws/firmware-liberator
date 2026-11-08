#!/usr/bin/env python3
"""The website's settings on Cloudflare, written down here with why.

    python3 tools/repo/cloudflare_settings.py          what differs from them
    python3 tools/repo/cloudflare_settings.py apply    set them

With CLOUDFLARE_API_TOKEN, in the environment or .env: to check, a token
allowed to read the zone, its settings, its single redirects and its rate
limiting rules, and the account's R2 buckets; to apply, one allowed to edit
them all. `apply` changes only what differs. The Workers, and the domains
pointing at them, are their wrangler.jsonc's, which the website workflow
deploys. website/README.md says which token is which.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.autoflash import credentials  # noqa: E402

ZONE = "firmwareliberator.com"
API = "https://api.cloudflare.com/client/v4"

# Plain HTTP is answered with a redirect to HTTPS, and TLS older than 1.2,
# which every browser of the last decade outgrew, is refused.
SETTINGS = {"always_use_https": "on", "min_tls_version": "1.2"}

# www.firmwareliberator.com, which wrangler.jsonc also points at the Worker,
# is sent on to firmwareliberator.com, keeping the path and query, so the
# site has one address. Cloudflare runs this before the Worker.
REDIRECT_PHASE = "http_request_dynamic_redirect"
REDIRECTS = [{
    "ref": "www-to-apex",
    "description": f"www.{ZONE} to {ZONE}",
    "expression": f'(http.host eq "www.{ZONE}")',
    "action": "redirect",
    "action_parameters": {"from_value": {
        "status_code": 301,
        "target_url": {"expression": f'concat("https://{ZONE}", http.request.uri.path)'},
        "preserve_query_string": True,
    }},
    "enabled": True,
}]


# Where the API Worker keeps the readouts the tool sends
# (website/api/index.js), as its wrangler.jsonc binds it. Private: a bucket
# is made with no public access, and nothing here gives it any. Everything
# under readouts/ is deleted READOUTS_KEPT_DAYS after it came in, which with
# the Worker's daily limit keeps the bucket within R2's free 10 GB; one worth
# keeping is moved under kept/ by hand (docs/readouts.md).
READOUTS_BUCKET = "firmware-liberator-readouts"
READOUTS_KEPT_DAYS = 30
READOUTS_LIFECYCLE = {"rules": [{
    "id": "readouts-expire",
    "enabled": True,
    "conditions": {"prefix": "readouts/"},
    "deleteObjectsTransition": {"condition": {"type": "Age", "maxAge": READOUTS_KEPT_DAYS * 86400}},
}]}


# Before the API Worker (website/api/index.js) runs at all: more than
# RATE_LIMIT_REQUESTS from one address in 10 seconds is blocked for 10 seconds,
# so a flood never reaches the Worker, where every request, even one it turns
# away, counts against the account's 100,000 a day on Workers' free plan; past
# that, every Worker on the account fails until the next day. A real readout is
# one request. The free plan allows one such rule, of 10-second periods, by
# address and data centre.
API_HOST = f"api.{ZONE}"
RATE_LIMIT_PHASE = "http_ratelimit"
RATE_LIMIT_REQUESTS = 5
RATE_LIMITS = [{
    "ref": "api-flood",
    "description": f"{API_HOST}: at most {RATE_LIMIT_REQUESTS} requests in 10 seconds from one address",
    "expression": f'(http.host eq "{API_HOST}")',
    "action": "block",
    "ratelimit": {"characteristics": ["cf.colo.id", "ip.src"], "period": 10,
                  "requests_per_period": RATE_LIMIT_REQUESTS, "mitigation_timeout": 10},
    "enabled": True,
}]
# Each zone ruleset this script keeps, by its phase: the rules as written here.
RULESETS = {REDIRECT_PHASE: ("redirects", REDIRECTS), RATE_LIMIT_PHASE: ("rate limits", RATE_LIMITS)}


class SettingsError(RuntimeError):
    pass


def token() -> str:
    value = os.environ.get("CLOUDFLARE_API_TOKEN")
    if value:
        return value
    try:
        env_file = credentials.parse(credentials.ENV_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        env_file = {}
    value = env_file.get("CLOUDFLARE_API_TOKEN")
    if not value:
        raise SettingsError("no CLOUDFLARE_API_TOKEN: in the environment or .env")
    return value


def api(path: str, method: str = "GET", body: Any = None, ok_missing: bool = False, whole: bool = False) -> Any:
    """The answer's result, or with `whole` all of it, its result_info too."""
    request = urllib.request.Request(
        API + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.load(response)
            return answer if whole else answer["result"]
    except urllib.error.HTTPError as error:
        if ok_missing and error.code == 404:
            return None
        detail = error.read().decode("utf-8", "replace")
        try:
            detail = "; ".join(e.get("message", "") for e in json.loads(detail)["errors"]) or detail
        except (ValueError, KeyError, TypeError):
            pass
        raise SettingsError(f"{method} {path}: HTTP {error.code}: {detail}") from None
    except OSError as error:
        raise SettingsError(f"{method} {path}: {error}") from None


def comparable(rule: dict[str, Any], written: dict[str, Any] | None = None) -> dict[str, Any]:
    """What a rule says, as `written` (REDIRECTS' first, by default) words it,
    from what Cloudflare returns: only the keys written here."""
    return {key: rule.get(key) for key in (written or REDIRECTS[0])}


def differences() -> list[tuple[str, str, Any]]:
    """(what, how it differs, the change that makes it right)."""
    zones = api(f"/zones?name={ZONE}")
    if not zones:
        raise SettingsError(f"the token cannot see the zone {ZONE}")
    zone = f"/zones/{zones[0]['id']}"
    found: list[tuple[str, str, Any]] = []
    for key, want in SETTINGS.items():
        have = api(f"{zone}/settings/{key}")["value"]
        if have != want:
            found.append((key, f"is {have}, should be {want}", ("PATCH", f"{zone}/settings/{key}", {"value": want})))
    for phase, (what, rules) in RULESETS.items():
        entrypoint = api(f"{zone}/rulesets/phases/{phase}/entrypoint", ok_missing=True)
        have = [comparable(rule, rules[0]) for rule in (entrypoint or {}).get("rules") or []]
        if have != rules:
            found.append((what, f"are {[rule['ref'] for rule in have]}, should be {[r['ref'] for r in rules]}",
                          ("PUT", f"{zone}/rulesets/phases/{phase}/entrypoint", {"rules": rules})))
    account = f"/accounts/{zones[0]['account']['id']}/r2/buckets"
    lifecycle = ("PUT", f"{account}/{READOUTS_BUCKET}/lifecycle", READOUTS_LIFECYCLE)
    if api(f"{account}/{READOUTS_BUCKET}", ok_missing=True) is None:
        found.append((READOUTS_BUCKET, "does not exist", ("POST", account, {"name": READOUTS_BUCKET})))
        found.append((f"{READOUTS_BUCKET} lifecycle", "is not set", lifecycle))
    else:
        rules = (api(lifecycle[1], ok_missing=True) or {}).get("rules") or []
        have = [{key: rule.get(key) for key in READOUTS_LIFECYCLE["rules"][0]} for rule in rules]
        if have != READOUTS_LIFECYCLE["rules"]:
            found.append((f"{READOUTS_BUCKET} lifecycle", f"has {[rule.get('id') for rule in rules]}, should have "
                          "readouts-expire", lifecycle))
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["apply"]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        found = differences()
        if not found:
            print(f"{ZONE}: every setting is as tools/repo/cloudflare_settings.py says.")
            return 0
        for what, how, _ in found:
            print(f"  {what}: {how}")
        if not args:
            print("python3 tools/repo/cloudflare_settings.py apply sets them.")
            return 1
        for what, _, (method, path, body) in found:
            api(path, method, body)
            print(f"  {what}: set")
    except SettingsError as error:
        print(f"cloudflare_settings: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
