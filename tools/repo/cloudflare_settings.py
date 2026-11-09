#!/usr/bin/env python3
"""The website's settings on Cloudflare, written down here with why.

    python3 tools/repo/cloudflare_settings.py          what differs from them
    python3 tools/repo/cloudflare_settings.py apply    set them
    python3 tools/repo/cloudflare_settings.py ci       what differs, and that the
                                                       token reads no more than CI's should
    python3 tools/repo/cloudflare_settings.py deploy-token
                                                       that the token reads no more than
                                                       the deploy token should

With CLOUDFLARE_API_TOKEN, in the environment or .env: to check, a token
allowed to read the zone, its settings, DNS records, single redirects, rate
limiting rules and Email Routing rules, and the account's R2 buckets and
Email Routing addresses; to apply, one allowed to edit them all. With
CLOUDFLARE_EMAIL_DESTINATION and CLOUDFLARE_EMAIL_FORWARD too, the same
way: the address mail is forwarded to, and the domain's addresses forwarded
there. They are private, so never written here. `apply` changes only what differs. The Workers, and the domains
pointing at them, are their wrangler.jsonc's, which the website workflow
deploys. website/README.md says which token is which.

The deploy token's permissions are checked too, but only with a token that
can also read the account's API tokens and Workers: the apply token, never
CI's, as that would show every token in the account. A token is changed in
the dashboard, never by `apply`.

`ci`, as the cloudflare-settings workflow runs it, also tries reads its
token must be refused, failing where any works: other zones and accounts,
Workers, and what CI_REFUSED lists. A token given more than it should be is
found that way, by what it can see; reads alone cannot show what it can
change.
"""

from __future__ import annotations

import json
import os
import re
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
# keeping is moved under kept/ by hand (.github/readouts.md).
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
# Mail to the domain is received by Cloudflare's Email Routing, which made
# and keeps its own DNS records: MX, its DKIM key and SPF. They are checked
# against the list Email Routing gives of what it needs, so its key is not
# copied here. Each address EMAIL_FORWARD names, by the part before the @
# ("contact", say, separated by commas), is forwarded to EMAIL_DESTINATION,
# the maintainer's, a destination address the account has verified; mail to
# any other is dropped (the catch-all, as Cloudflare makes it, left off).
# Both are kept out of the repository, where scrapers would find them, and
# out of what this prints, as CI's log is public.
EMAIL_DESTINATION = "CLOUDFLARE_EMAIL_DESTINATION"
EMAIL_FORWARD = "CLOUDFLARE_EMAIL_FORWARD"
EMAIL_CATCH_ALL = {"name": "", "matchers": [{"type": "all"}], "actions": [{"type": "drop"}], "enabled": False}

# The zone's DNS records beyond Email Routing's and the Workers' custom
# domains' (wrangler makes those): any other record is a difference, and
# `apply` deletes it. Each proves something, and is public anyway.
DNS_RECORDS = [
    # DMARC: mail from the domain that passes neither SPF nor DKIM is put in
    # spam, and receivers' reports go to Cloudflare's DMARC Management
    # (dashboard, Email, DMARC Management).
    {"type": "TXT", "name": f"_dmarc.{ZONE}",
     "content": "v=DMARC1; p=quarantine; rua=mailto:06eff54c46654ef69afd474e0d4cece5@dmarc-reports.cloudflare.net"},
    # Proves the domain is ours to Google Search Console.
    {"type": "TXT", "name": ZONE, "content": "google-site-verification=K7JzvlKuELWLFIHNqGRPc_D_ZWduaiq_TIj-jr0yve4"},
    # Verifies the domain for adamsthws' GitHub Pages, so no one else's
    # Pages site can claim it.
    {"type": "TXT", "name": f"_github-pages-challenge-adamsthws.{ZONE}", "content": "ee553f2ec46fa330f7f3221f7399ec"},
]

# The website workflow's token (website/README.md): the zone's Worker routes
# and DNS records, for the Workers' custom domains, and its two Workers, by
# name - no other Worker in the account.
DEPLOY_TOKEN = "firmware-liberator-deploy-workers-ci-read-write"
DEPLOY_ZONE_PERMISSIONS = {"Zone Read", "Workers Routes Write", "DNS Write"}
DEPLOY_WORKERS = {"firmware-liberator", "firmware-liberator-api"}
DEPLOY_WORKERS_PERMISSIONS = {"Individual Workers Editor"}
# Each zone ruleset this script keeps, by its phase: the rules as written here.
RULESETS = {REDIRECT_PHASE: ("redirects", REDIRECTS), RATE_LIMIT_PHASE: ("rate limits", RATE_LIMITS)}


class SettingsError(RuntimeError):
    pass


def variable(name: str) -> str:
    """`name` from the environment, or else .env."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        env_file = credentials.parse(credentials.ENV_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        env_file = {}
    value = env_file.get(name)
    if not value:
        raise SettingsError(f"no {name}: in the environment or .env")
    return value


def token() -> str:
    return variable("CLOUDFLARE_API_TOKEN")


def api(path: str, method: str = "GET", body: Any = None, ok_missing: bool = False, whole: bool = False,
        ok_forbidden: bool = False) -> Any:
    """The answer's result, or with `whole` all of it, its result_info too;
    None for a 404 with `ok_missing`, or with `ok_forbidden` a refusal: 403,
    or 401 as some products answer a token not allowed them."""
    request = urllib.request.Request(
        API + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            answer = json.load(response)
            return answer if whole else answer["result"]
    except urllib.error.HTTPError as error:
        if (ok_missing and error.code == 404) or (ok_forbidden and error.code in (401, 403)):
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


def deploy_token_policies(account: str, zone_id: str) -> list[tuple[frozenset[str], frozenset[str]]] | None:
    """What the deploy token is allowed, as (permissions, what on) pairs - the
    zone by its name, Workers by theirs - or None where this token cannot
    read the account's tokens or Workers."""
    tokens = api(f"/accounts/{account}/tokens", ok_forbidden=True)
    scripts = api(f"/accounts/{account}/workers/scripts", ok_forbidden=True)
    if tokens is None or scripts is None:
        return None
    workers = {f"com.cloudflare.edge.worker.script.{script.get('tag')}": script.get("id") for script in scripts}
    zone = f"com.cloudflare.api.account.zone.{zone_id}"
    found = [token for token in tokens if token.get("name") == DEPLOY_TOKEN and token.get("status") == "active"]
    policies = []
    for policy in (found[0].get("policies") or []) if found else []:
        on = set()
        for key, value in (policy.get("resources") or {}).items():
            # An account's resources name its Workers inside it.
            for inner in value if isinstance(value, dict) else {key: value}:
                on.add(ZONE if inner == zone else workers.get(inner, inner))
        denied = "" if policy.get("effect") == "allow" else "denied: "
        policies.append((frozenset(denied + str(group.get("name")) for group in policy.get("permission_groups") or []),
                         frozenset(on)))
    return policies


def in_order(policies: list[tuple[frozenset[str], frozenset[str]]]) -> list[tuple[list[str], list[str]]]:
    """Policies in an order of their own, to compare and to show: a set's
    text is in no fixed order, so sorting by it would differ run to run."""
    return sorted((sorted(names), sorted(on)) for names, on in policies)


DEPLOY_POLICIES = in_order([(frozenset(DEPLOY_ZONE_PERMISSIONS), frozenset({ZONE})),
                            (frozenset(DEPLOY_WORKERS_PERMISSIONS), frozenset(DEPLOY_WORKERS))])


# What each of CI's tokens must be refused, each a sign of permissions it was
# never meant to have. CI's read token needs none of it to check the
# settings, and each was refused it when written down (2026-11-08), so a
# failure there is a change. Both read the zone's DNS records: CI's to check
# them, the deploy token as it edits its Workers' custom domains there. The
# deploy token edits its Workers, so it may read those too; its value lives
# only in GitHub, so its list was first checked by CI itself.
REFUSED = [
    ("the account's API tokens", "/accounts/{account}/tokens"),
    ("your user's details", "/user"),
    ("the account's members", "/accounts/{account}/members"),
    ("the account's audit logs", "/accounts/{account}/audit_logs"),
    ("the account's billing", "/accounts/{account}/billing/profile"),
    ("the account's Pages projects", "/accounts/{account}/pages/projects"),
    ("the zone's SSL certificates", "/zones/{zone}/ssl/certificate_packs"),
    ("the account's KV namespaces", "/accounts/{account}/storage/kv/namespaces"),
    ("the account's D1 databases", "/accounts/{account}/d1/database"),
]
CI_REFUSED = [*REFUSED,
              ("the API Worker's settings and bindings",
               "/accounts/{account}/workers/scripts/firmware-liberator-api/settings")]
DEPLOY_REFUSED = REFUSED


def token_reach(account: str, zone: str, workers: set[str], refused: list[tuple[str, str]]) -> list[tuple[str, str, Any]]:
    """What the token in use can see that it should not: a zone but this one,
    an account but this one, Workers other than `workers` (the list answers,
    filtered to those it may see), or anything `refused` lists."""
    found: list[tuple[str, str, Any]] = []
    others = sorted(item.get("name") for item in api("/zones?per_page=50") or [] if item.get("name") != ZONE)
    if others:
        found.append(("this token", f"can see other zones ({', '.join(others)}): give it {ZONE} alone", None))
    accounts = api("/accounts?per_page=50") or []
    if len(accounts) != 1:
        found.append(("this token", f"can see {len(accounts)} accounts: give it this one alone", None))
    seen = {script.get("id") for script in api(f"/accounts/{account}/workers/scripts", ok_forbidden=True) or []}
    if seen != workers:
        wanted = ", ".join(sorted(workers)) or "none"
        found.append(("this token", f"can see the Workers {sorted(seen)}: it should see {wanted}", None))
    for what, path in refused:
        if api(path.format(account=account, zone=zone), ok_forbidden=True) is not None:
            found.append(("this token", f"can read {what}: it should be refused", None))
    return found


def ci_token_reach(account: str, zone: str) -> list[tuple[str, str, Any]]:
    """What CI's read token can see that it should not: it needs no Worker."""
    return token_reach(account, zone, set(), CI_REFUSED)


def deploy_token_reach() -> list[tuple[str, str, Any]]:
    """What the deploy token can see that it should not: anything but its two
    Workers, this zone and this account, and what it edits."""
    zones = api(f"/zones?name={ZONE}")
    if not zones:
        raise SettingsError(f"the token cannot see the zone {ZONE}")
    return token_reach(zones[0]["account"]["id"], zones[0]["id"], DEPLOY_WORKERS, DEPLOY_REFUSED)


def text(record: dict[str, Any]) -> str:
    """A record's content as compared: a TXT record's quoted strings joined,
    as Cloudflare lists a long one split and Email Routing whole; a host
    name without its final dot."""
    content = record.get("content") or ""
    if record.get("type") == "TXT" and content.startswith('"'):
        return "".join(re.findall(r'"((?:[^"\\]|\\.)*)"', content))
    return content.rstrip(".") if record.get("type") in ("MX", "CNAME") else content


def key(record: dict[str, Any]) -> tuple[Any, ...]:
    return record.get("type"), record.get("name"), text(record), record.get("priority")


def said(record: dict[str, Any]) -> str:
    return f"{record['type']} {record['name']} {text(record)!r}"


def dns_differences(zone: str) -> list[tuple[str, str, Any]]:
    """The zone's records against DNS_RECORDS and what Email Routing needs,
    the Workers' custom domains aside."""
    answer = api(f"{zone}/dns_records?per_page=500", whole=True)
    have = [r for r in answer["result"] if not (r.get("meta") or {}).get("origin_worker_id")]
    if answer["result_info"]["total_count"] > len(answer["result"]):
        raise SettingsError("the zone has more DNS records than one page lists")
    wanted = DNS_RECORDS + (api(f"{zone}/email/routing/dns") or [])
    found = []
    for record in wanted:
        if not any(key(r) == key(record) for r in have):
            body = {**record, "content": text(record), "ttl": 1, "proxied": False}
            found.append((said(record), "is missing", ("POST", f"{zone}/dns_records", body)))
    for record in have:
        if not any(key(r) == key(record) for r in wanted):
            found.append((said(record), "is not written down", ("DELETE", f"{zone}/dns_records/{record['id']}", None)))
    return found


def email_rules(destination: str) -> list[dict[str, Any]]:
    """A rule for each address EMAIL_FORWARD names, forwarding it to
    `destination`, in an order of their own to compare."""
    names = sorted({name.strip().lower() for name in variable(EMAIL_FORWARD).split(",") if name.strip()})
    return [{"name": "", "matchers": [{"type": "literal", "field": "to", "value": f"{name}@{ZONE}"}],
             "actions": [{"type": "forward", "value": [destination]}], "enabled": True} for name in names]


def email_differences(zone: str, account: str) -> list[tuple[str, str, Any]]:
    """Email Routing: on, its rules and catch-all as written, and the
    destination address the account's one, verified. Addresses are never
    printed."""
    found: list[tuple[str, str, Any]] = []
    want = variable(EMAIL_DESTINATION).strip().lower()
    written = email_rules(want)
    if not api(f"{zone}/email/routing").get("enabled"):
        found.append(("Email Routing", "is off", ("POST", f"{zone}/email/routing/enable", {})))
    fields = ("name", "matchers", "actions", "enabled")
    rules = [rule for rule in api(f"{zone}/email/routing/rules?per_page=50") or []
             if rule.get("matchers") != EMAIL_CATCH_ALL["matchers"]]
    have = [{field: rule.get(field) for field in fields} for rule in rules]
    if sorted(have, key=lambda rule: json.dumps(rule, sort_keys=True)) != written:
        found += [(f"email rule {n}", "is not as written", ("DELETE", f"{zone}/email/routing/rules/{rule['id']}", None))
                  for n, rule in enumerate(rules, 1)]
        found += [(f"email rule {n}", f"is to be made, from {EMAIL_FORWARD}",
                   ("POST", f"{zone}/email/routing/rules", rule)) for n, rule in enumerate(written, len(rules) + 1)]
    catch_all = api(f"{zone}/email/routing/rules/catch_all")
    if {field: catch_all.get(field) for field in fields} != EMAIL_CATCH_ALL:
        found.append(("email catch-all", f"{'forwards' if catch_all.get('enabled') else 'is off'} as written "
                      "nowhere", ("PUT", f"{zone}/email/routing/rules/catch_all", EMAIL_CATCH_ALL)))
    path = f"/accounts/{account}/email/routing/addresses"
    have = {item.get("email", "").lower(): item for item in api(f"{path}?per_page=50") or []}
    if want not in have:
        found.append((EMAIL_DESTINATION, "is not a destination address", ("POST", path, {"email": want})))
    elif not have[want].get("verified"):
        found.append((EMAIL_DESTINATION, "is not verified: follow the link Cloudflare mailed it", None))
    if len(have) - (want in have):
        found.append(("destination addresses", f"{len(have) - (want in have)} besides {EMAIL_DESTINATION}", None))
    return found


def differences(ci: bool = False) -> list[tuple[str, str, Any]]:
    """(what, how it differs, the change that makes it right: None for one
    made in the dashboard). With `ci`, what CI's token can see that it
    should not too."""
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
    found += email_differences(zone, zones[0]["account"]["id"])
    found += dns_differences(zone)
    have = deploy_token_policies(zones[0]["account"]["id"], zones[0]["id"])
    if have is None:
        print(f"  {DEPLOY_TOKEN}: not checked - this token cannot read the account's API tokens and Workers")
    elif in_order(have) != DEPLOY_POLICIES:
        def say(policies):
            return "; ".join(f"{', '.join(names)} on {', '.join(on)}" for names, on in policies) \
                or "nothing (no such token)"
        found.append((DEPLOY_TOKEN, f"allows {say(in_order(have))}, should allow {say(DEPLOY_POLICIES)}", None))
    if ci:
        found += ci_token_reach(zones[0]["account"]["id"], zones[0]["id"])
    return found


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args not in ([], ["apply"], ["ci"], ["deploy-token"]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    if args == ["deploy-token"]:
        try:
            found = deploy_token_reach()
        except SettingsError as error:
            print(f"cloudflare_settings: {error}", file=sys.stderr)
            return 1
        for what, how, _ in found:
            print(f"  {what}: {how}")
        if found:
            print("Change the deploy token in the dashboard (website/README.md).")
            return 1
        print(f"The deploy token sees {ZONE}, its Workers and no more.")
        return 0
    try:
        found = differences(ci=args == ["ci"])
        if not found:
            print(f"{ZONE}: every setting is as tools/repo/cloudflare_settings.py says.")
            return 0
        for what, how, _ in found:
            print(f"  {what}: {how}")
        if args != ["apply"]:
            print("python3 tools/repo/cloudflare_settings.py apply sets them; a token is changed in the dashboard.")
            return 1
        left = 0
        for what, _, change in found:
            if change is None:
                print(f"  {what}: left - change it in the dashboard")
                left = 1
                continue
            method, path, body = change
            api(path, method, body)
            print(f"  {what}: set")
    except SettingsError as error:
        print(f"cloudflare_settings: {error}", file=sys.stderr)
        return 1
    return left


if __name__ == "__main__":
    sys.exit(main())
