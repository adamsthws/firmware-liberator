#!/usr/bin/env python3
"""The website's settings on Cloudflare, written down here with why.

    python3 tools/repo/cloudflare_settings.py          what differs from them
    python3 tools/repo/cloudflare_settings.py apply    set them
    python3 tools/repo/cloudflare_settings.py ci       what differs, and that the
                                                       token reads no more than CI's should
    python3 tools/repo/cloudflare_settings.py deploy-token
                                                       that the token reads no more than
                                                       the deploy token should
    python3 tools/repo/cloudflare_settings.py rotate NAME
                                                       a new value for the API Worker's
                                                       secret NAME, one made here

With CLOUDFLARE_API_TOKEN, in the environment or .env.infra (as
.env.infra.example shows; not .env, whose names are the tool's): to check, a token
allowed to read the zone, its settings, DNS records, single redirects, rate
limiting rules and Email Routing rules, and the account's R2 buckets, D1
databases and Email Routing addresses; to apply, one allowed to edit them all. With
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
import secrets
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
# The maintainer's tokens and private settings for the project's services,
# apart from .env, which the tool reads and which refuses a name it does not
# know.
ENV_FILE = ROOT / ".env.infra"

from tools.autoflash import credentials  # noqa: E402

ZONE = "firmwareliberator.com"
API = "https://api.cloudflare.com/client/v4"

# Email addresses in the site's pages are hidden from scrapers, shown to a
# browser by Cloudflare's script (on by default; kept on here). It covers
# HTML Cloudflare serves, but not what a Worker's own code writes into a
# page, nor an address inside a tag's attribute other than a link's href.
# Plain HTTP is answered with a redirect to HTTPS, and TLS older than 1.2,
# which every browser of the last decade outgrew, is refused.
SETTINGS = {"always_use_https": "on", "min_tls_version": "1.2", "email_obfuscation": "on"}

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
# is made with no public access, and nothing here gives it any. Nothing in it
# expires: a readout is kept only if it adds something, and kept for good, the
# Worker's limit on the bytes of them all keeping the bucket within R2's free
# 10 GB (.github/readouts.md). So it has no lifecycle rule, and one added in
# the dashboard is a difference that `apply` takes away.
READOUTS_BUCKET = "firmware-liberator-readouts"
READOUTS_LIFECYCLE = {"rules": []}


# The API Worker's database (website/api/database.js), as its wrangler.jsonc
# binds it: made here, and brought up to date by running each file in
# MIGRATIONS not yet run, in name order, recorded in d1_migrations as wrangler
# records them. Only with the maintainer's token: D1 permissions reach every
# database in the account, so CI's token is refused them (REFUSED) and `ci`
# leaves the database to the hand check and to the public snapshot's check.
D1_DATABASE = "firmware-liberator-api"
API_WRANGLER = ROOT / "website" / "api" / "wrangler.jsonc"
MIGRATIONS = ROOT / "website" / "api" / "migrations"
D1_PLACEHOLDER = "00000000-0000-0000-0000-000000000000"
D1_MIGRATIONS_TABLE = ("CREATE TABLE IF NOT EXISTS d1_migrations (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "name TEXT UNIQUE, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP NOT NULL);")


# Before the API Worker (website/api/index.js) runs at all: more than
# RATE_LIMIT_REQUESTS from one address in 10 seconds is blocked for 10 seconds,
# so a flood never reaches the Worker, where every request, even one it turns
# away, runs its code and is counted. The account is on Workers Paid (since
# 2026-11-10): what is past its allowance a month is billed, not stopped, so
# a flood costs money; on the free plan, past 100,000 a day, the API (any
# Worker that runs code) would fail until the next day, while the website,
# static files alone, which are free and unlimited, would still be served.
# A real readout is one request. The free plan allows one such rule, of 10-second periods, by
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
# Before the API Worker too: only the tool's requests, a POST to one of its
# paths, get through; anything else is blocked at Cloudflare's edge, so it
# never counts against the account's Workers requests (the rate rule above
# says why that matters). The free plan allows five such rules.
FIREWALL_PHASE = "http_request_firewall_custom"
# The paths, as the Worker routes them, and the methods each takes
# (website/test/api.test.mjs holds the two alike): a POST from the tool, and
# for /forget, opened from the link in an email, a GET too.
API_METHODS = {"/readout": ("POST",), "/wanted": ("POST",), "/notify": ("POST",), "/reports": ("POST",), "/confirm": ("GET", "POST"), "/forget": ("GET", "POST")}
API_PATHS = tuple(API_METHODS)


def _allowed(methods: tuple[str, ...]) -> str:
    """The firewall expression's term for the paths taking `methods`."""
    paths = " ".join(f'"{path}"' for path, taken in API_METHODS.items() if taken == methods)
    said = " ".join(f'"{method}"' for method in methods)
    return f"(http.request.method in {{{said}}} and http.request.uri.path in {{{paths}}})"


FIREWALL_RULES = [{
    "ref": "api-only-the-tool",
    "description": f"{API_HOST}: only " + "; ".join(f"{'/'.join(methods)} to {', '.join(p for p, m in API_METHODS.items() if m == methods)}"
                                                    for methods in dict.fromkeys(API_METHODS.values())),
    "expression": (f'(http.host eq "{API_HOST}" and not ('
                   + " or ".join(_allowed(methods) for methods in dict.fromkeys(API_METHODS.values())) + "))"),
    "action": "block",
    "enabled": True,
}]

# The public figures the API Worker writes each night (website/api/nightly.js)
# are served from a bucket of their own, holding nothing else, at STATS_HOST
# alone: its custom domain, made here, which makes its own DNS record (left
# out of the DNS check, as the Workers' are); R2's own public address for it
# (r2.dev) stays off. Cloudflare does not cache JSON by itself, so a cache
# rule has the figures served from its cache for an hour: a page view or a
# crawl then costs no read of the bucket.
STATS_BUCKET = "firmware-liberator-stats"
STATS_HOST = f"stats.{ZONE}"
CACHE_PHASE = "http_request_cache_settings"
CACHE_RULES = [{
    "ref": "stats-cached",
    "description": f"{STATS_HOST}: served from Cloudflare's cache for an hour",
    "expression": f'(http.host eq "{STATS_HOST}")',
    "action": "set_cache_settings",
    "action_parameters": {"cache": True, "edge_ttl": {"mode": "override_origin", "default": 3600},
                          "browser_ttl": {"mode": "override_origin", "default": 3600}},
    "enabled": True,
}]

# The API Worker's secrets (website/api/wrangler.jsonc says what each is for),
# set by `apply` and checked here by name with the maintainer's token - a
# value is never read back, and CI's token is refused the Worker's settings.
# - "made": a random value `apply` makes when it is missing, and `rotate NAME`
#   replaces: nothing outside the Worker needs to know it.
# - "kept": one that must never change, kept privately as API_<NAME> in the
#   environment or .env.infra, from where `apply` sets it when it is missing;
#   never made or rotated here.
# - "destination": the maintainer's address, EMAIL_DESTINATION (below), which
#   the API's nightly job emails when a limit was reached (alerts.js): set
#   from it, as it is kept for Email Routing already, and never printed.
API_WORKER = "firmware-liberator-api"
API_SECRETS = {"DEVICE_HASH_KEY_PERMANENT": "kept", "SENDER_KEY": "made", "ALERTS_RECIPIENT_EMAIL": "destination"}

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
def caa(authority: str) -> dict[str, Any]:
    """A CAA record letting `authority` issue certificates for the domain,
    its subdomains and wildcards; the API takes it as data, and lists it as
    content."""
    return {"type": "CAA", "name": ZONE, "content": f'0 issue "{authority}"',
            "data": {"flags": 0, "tag": "issue", "value": authority}}


DNS_RECORDS = [
    # Only the certificate authorities Cloudflare's Universal SSL uses may
    # issue a certificate for the domain: one from any other, for someone
    # who has fooled it, is refused. Cloudflare adds its own CAs' records
    # beside these, unlisted, should it take on another.
    *(caa(authority) for authority in ("letsencrypt.org", "pki.goog", "ssl.com")),
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
RULESETS = {REDIRECT_PHASE: ("redirects", REDIRECTS), RATE_LIMIT_PHASE: ("rate limits", RATE_LIMITS),
            FIREWALL_PHASE: ("firewall rules", FIREWALL_RULES), CACHE_PHASE: ("cache rules", CACHE_RULES)}


class SettingsError(RuntimeError):
    pass


def variable(name: str) -> str:
    """`name` from the environment, or else .env.infra."""
    value = os.environ.get(name)
    if value:
        return value
    try:
        env_file = credentials.parse(ENV_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        env_file = {}
    value = env_file.get(name)
    if not value:
        raise SettingsError(f"no {name}: in the environment or .env.infra")
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


def dnssec_differences(zone: str) -> list[tuple[str, str, Any]]:
    """DNSSEC on: answers signed, so a resolver can tell them from forged
    ones. Cloudflare is the registrar, and puts the DS record in .com itself
    once it is turned on: pending until then."""
    status = api(f"{zone}/dnssec").get("status")
    if status == "active":
        return []
    if status == "pending":
        return [("DNSSEC", "is pending", "wait: Cloudflare, the registrar, adds the DS record to .com itself")]
    return [("DNSSEC", f"is {status}", ("PATCH", f"{zone}/dnssec", {"status": "active"}))]


def dns_differences(zone: str) -> list[tuple[str, str, Any]]:
    """The zone's records against DNS_RECORDS and what Email Routing needs,
    the Workers' custom domains aside."""
    answer = api(f"{zone}/dns_records?per_page=500", whole=True)
    have = [r for r in answer["result"] if not (r.get("meta") or {}).get("origin_worker_id")
            and r.get("name") != STATS_HOST]
    if answer["result_info"]["total_count"] > len(answer["result"]):
        raise SettingsError("the zone has more DNS records than one page lists")
    wanted = DNS_RECORDS + (api(f"{zone}/email/routing/dns") or []) + sending_records(zone)
    found = []
    for record in wanted:
        if not any(key(r) == key(record) for r in have):
            body = {**record, "content": text(record), "ttl": 1, "proxied": False}
            if "data" in record:
                del body["content"]
            found.append((said(record), "is missing", ("POST", f"{zone}/dns_records", body)))
    for record in have:
        if not any(key(r) == key(record) for r in wanted):
            found.append((said(record), "is not written down", ("DELETE", f"{zone}/dns_records/{record['id']}", None)))
    return found


# The domain the API Worker's email is sent from (Cloudflare Email Service,
# its EMAIL binding: the sign-ups' confirmations and the nightly alerts),
# onboarded for sending as the zone itself, so mail comes from
# updates@firmwareliberator.com. Onboarding makes Cloudflare want its own DNS
# records - a bounce subdomain's MX, SPF and DKIM - which the DNS check then
# expects as it does Email Routing's; but for its DMARC record: DNS_RECORDS
# has the zone's one, and a second would void both.
SENDING_DOMAIN = ZONE


def sending_domains(zone: str) -> list[dict[str, Any]]:
    """The zone's domains onboarded for Email Service's sending."""
    return api(f"{zone}/email/sending/subdomains") or []


def sending_records(zone: str, domains: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """The DNS records Email Service wants for each domain onboarded, as it
    lists them, but its DMARC one: the zone has its own (DNS_RECORDS)."""
    records = []
    for domain in sending_domains(zone) if domains is None else domains:
        ident = domain.get("id") or domain.get("tag")
        for record in api(f"{zone}/email/sending/subdomains/{ident}/dns") or []:
            if not str(record.get("name", "")).startswith("_dmarc."):
                records.append({field: record[field] for field in ("type", "name", "content", "priority")
                                if record.get(field) is not None})
    return records


def onboard_sending(zone: str) -> list[str]:
    """Onboard SENDING_DOMAIN for sending, and make the DNS records it then
    wants that the zone lacks; what is left to do by hand, if anything."""
    made = api(f"{zone}/email/sending/subdomains", "POST", {"name": SENDING_DOMAIN})
    have = [key(r) for r in api(f"{zone}/dns_records?per_page=500", whole=True)["result"]]
    for record in sending_records(zone, [made]):
        if key(record) not in have:
            api(f"{zone}/dns_records", "POST", {**record, "content": text(record), "ttl": 1, "proxied": False})
    return []


def sending_differences(zone: str) -> list[tuple[str, str, Any]]:
    """SENDING_DOMAIN onboarded for Email Service's sending, and enabled."""
    found = [domain for domain in sending_domains(zone) if domain.get("name") == SENDING_DOMAIN]
    if not found:
        return [(f"Email Sending for {SENDING_DOMAIN}", "is not onboarded", lambda: onboard_sending(zone))]
    if not found[0].get("enabled"):
        return [(f"Email Sending for {SENDING_DOMAIN}", "is onboarded but not enabled",
                 "enable it in the dashboard: Compute, Email Service, Email Sending")]
    return []


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


def jsonc(path: Path) -> Any:
    """A wrangler.jsonc: JSON with whole-line // comments, as these are written."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return json.loads("\n".join(line for line in lines if not line.lstrip().startswith("//")))


def bound_database_id() -> str:
    """The database id the API Worker's wrangler.jsonc binds as DB."""
    bound = [db for db in jsonc(API_WRANGLER).get("d1_databases") or [] if db.get("binding") == "DB"]
    if len(bound) != 1 or bound[0].get("database_name") != D1_DATABASE:
        raise SettingsError(f"{API_WRANGLER.relative_to(ROOT)} should bind {D1_DATABASE} as DB, once")
    return str(bound[0].get("database_id") or "")


def migration_files() -> list[str]:
    """The migrations, in the order they run. A name goes into the SQL that
    records it, so only the shape website/test/database.test.mjs holds them to."""
    names = sorted(path.name for path in MIGRATIONS.glob("*.sql"))
    for name in names:
        if not re.fullmatch(r"\d{4}_[a-z0-9_]+\.sql", name):
            raise SettingsError(f"{name}: a migration is named NNNN_words.sql")
    return names


def applied_migrations(query: str) -> list[str]:
    """The migrations d1_migrations records, in the order they were run: none
    for a database never migrated, which has no such table."""
    try:
        answer = api(query, "POST", {"sql": "SELECT name FROM d1_migrations ORDER BY id"})
    except SettingsError as error:
        if "no such table" in str(error):
            return []
        raise
    return [row["name"] for row in answer[0]["results"]]


def database_differences(account: str) -> list[tuple[str, str, Any]]:
    """The API's database: there, bound by its id, and every migration run, in order."""
    path = f"/accounts/{account}/d1/database"
    found_db = [db for db in api(f"{path}?name={D1_DATABASE}") or [] if db.get("name") == D1_DATABASE]
    if not found_db:
        return [(f"D1 {D1_DATABASE}", "does not exist", lambda: create_database(account))]
    uuid = found_db[0]["uuid"]
    found: list[tuple[str, str, Any]] = []
    bound = bound_database_id()
    if bound != uuid:
        found.append((str(API_WRANGLER.relative_to(ROOT)), f"binds the database {bound}, not {uuid}",
                      f"set its database_id to {uuid}, add that id to tools/repo/leak-scan-allow (the leak "
                      "scan takes any UUID for a device's), and commit both"))
    query = f"{path}/{uuid}/query"
    applied, files = applied_migrations(query), migration_files()
    if applied != files[:len(applied)]:
        found.append((f"D1 {D1_DATABASE}", f"has the migrations {applied}, which are not the first of {files}: "
                      "a migration was changed, removed or renamed after it was run", None))
        return found
    for name in files[len(applied):]:
        sql = (MIGRATIONS / name).read_text(encoding="utf-8")
        found.append((f"D1 {D1_DATABASE} migration {name}", "has not been run",
                      ("POST", query, {"sql": f"{D1_MIGRATIONS_TABLE}\n{sql}\n"
                                              f"INSERT INTO d1_migrations (name) VALUES ('{name}');"})))
    return found


def create_database(account: str) -> list[str]:
    """Make the API's database and run every migration in it: what is left
    for the maintainer to do, as lines to show."""
    api(f"/accounts/{account}/d1/database", "POST", {"name": D1_DATABASE})
    left = []
    for what, how, change in database_differences(account):
        if not isinstance(change, tuple):
            left.append(f"{what}: {how}: {change or 'change it in the dashboard'}")
            continue
        method, path, body = change
        api(path, method, body)
    return left


def stats_differences(account: str, zone_id: str) -> list[tuple[str, str, Any]]:
    """The public figures' bucket: there, at STATS_HOST, and nowhere else."""
    buckets = f"/accounts/{account}/r2/buckets"
    if api(f"{buckets}/{STATS_BUCKET}", ok_missing=True) is None:
        return [(STATS_BUCKET, "does not exist", ("POST", buckets, {"name": STATS_BUCKET})),
                (f"{STATS_BUCKET} at {STATS_HOST}", "is not set",
                 ("POST", f"{buckets}/{STATS_BUCKET}/domains/custom",
                  {"domain": STATS_HOST, "zoneId": zone_id, "enabled": True, "minTLS": "1.2"}))]
    found: list[tuple[str, str, Any]] = []
    domains = (api(f"{buckets}/{STATS_BUCKET}/domains/custom") or {}).get("domains") or []
    names = sorted(domain.get("domain") for domain in domains)
    if not any(domain.get("domain") == STATS_HOST and domain.get("enabled") for domain in domains):
        found.append((f"{STATS_BUCKET} at {STATS_HOST}", "is not set",
                      ("POST", f"{buckets}/{STATS_BUCKET}/domains/custom",
                       {"domain": STATS_HOST, "zoneId": zone_id, "enabled": True, "minTLS": "1.2"})))
    elif names != [STATS_HOST]:
        found.append((STATS_BUCKET, f"is served at {names}: it should be at {STATS_HOST} alone", None))
    if (api(f"{buckets}/{STATS_BUCKET}/domains/managed") or {}).get("enabled"):
        found.append((f"{STATS_BUCKET}'s r2.dev address", "is on: it should be off",
                      ("PUT", f"{buckets}/{STATS_BUCKET}/domains/managed", {"enabled": False})))
    return found


def put_secret(account: str, name: str, value: str) -> None:
    """Set the API Worker's secret `name`; its value goes in the request's
    body alone, never a command line or the screen."""
    api(f"/accounts/{account}/workers/scripts/{API_WORKER}/secrets", "PUT",
        {"name": name, "text": value, "type": "secret_text"})


def made_secret() -> str:
    """A new value for a "made" secret: 256 random bits."""
    return secrets.token_hex(32)


def secret_differences(account: str) -> list[tuple[str, str, Any]]:
    """The API Worker's secrets: each there, by name, and how `apply` sets one
    that is not: a "made" one made afresh, a "kept" one from API_<NAME>."""
    path = f"/accounts/{account}/workers/scripts/{API_WORKER}/secrets"
    have = api(path, ok_missing=True)
    if have is None:
        return [(f"{API_WORKER} secrets", "cannot be checked: the Worker is not there yet",
                 "deploy it, then run this again")]
    names = {secret.get("name") for secret in have}
    found: list[tuple[str, str, Any]] = []
    for name, how in API_SECRETS.items():
        if name in names:
            continue
        if how == "made":
            found.append((f"{API_WORKER} secret {name}", "is not set",
                          lambda name=name: put_secret(account, name, made_secret()) or []))
            continue
        if how == "destination":
            try:
                value = variable(EMAIL_DESTINATION)
            except SettingsError:
                found.append((f"{API_WORKER} secret {name}", "is not set",
                              f"put the maintainer's address in .env.infra as {EMAIL_DESTINATION} and run apply again"))
                continue
            found.append((f"{API_WORKER} secret {name}", "is not set",
                          lambda name=name, value=value: put_secret(account, name, value) or []))
            continue
        try:
            value = variable(f"API_{name}")
        except SettingsError:
            found.append((f"{API_WORKER} secret {name}", "is not set",
                          f"put its value, kept privately, in .env.infra as API_{name} and run apply again "
                          "(website/api/wrangler.jsonc says why it must never change)"))
            continue
        found.append((f"{API_WORKER} secret {name}", "is not set",
                      lambda name=name, value=value: put_secret(account, name, value) or []))
    return found


def rotate(name: str) -> int:
    """`rotate NAME`: a "made" secret replaced by a new value."""
    if API_SECRETS.get(name) != "made":
        made = [secret for secret, how in API_SECRETS.items() if how == "made"]
        print(f"cloudflare_settings: only a secret made here is rotated here: {', '.join(made)}"
              + (f" ({name} is kept, and must never change)" if name in API_SECRETS else ""), file=sys.stderr)
        return 2
    try:
        zones = api(f"/zones?name={ZONE}")
        if not zones:
            raise SettingsError(f"the token cannot see the zone {ZONE}")
        put_secret(zones[0]["account"]["id"], name, made_secret())
    except SettingsError as error:
        print(f"cloudflare_settings: {error}", file=sys.stderr)
        return 1
    print(f"  {API_WORKER} secret {name}: rotated")
    return 0


def differences(ci: bool = False) -> list[tuple[str, str, Any]]:
    """(what, how it differs, the change that makes it right, as main's apply
    takes it). With `ci`, what CI's token can see that it
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
    else:
        rules = (api(lifecycle[1], ok_missing=True) or {}).get("rules") or []
        if rules:
            found.append((f"{READOUTS_BUCKET} lifecycle", f"has {[rule.get('id') for rule in rules]}, should have "
                          "none: readouts are kept for good", lifecycle))
    found += stats_differences(zones[0]["account"]["id"], zones[0]["id"])
    if ci:
        print(f"  D1 {D1_DATABASE}: not checked - CI's token is refused D1, which reaches every database "
              "in the account; the hand check and the public snapshot's check cover it")
        print(f"  {API_WORKER} secrets: not checked - CI's token is refused the Worker's settings")
    else:
        found += database_differences(zones[0]["account"]["id"])
        found += secret_differences(zones[0]["account"]["id"])
    found += email_differences(zone, zones[0]["account"]["id"])
    found += sending_differences(zone)
    found += dns_differences(zone)
    found += dnssec_differences(zone)
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
    if len(args) == 2 and args[0] == "rotate":
        return rotate(args[1])
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
        # A change is (method, path, body) to send; a function, for one made
        # of several, giving back what it left; a sentence saying what to do
        # by hand; or None, for one made in the dashboard.
        for what, _, change in found:
            if change is None or isinstance(change, str):
                print(f"  {what}: left - {change or 'change it in the dashboard'}")
                left = 1
                continue
            if callable(change):
                for line in change():
                    print(f"  {line}")
                    left = 1
                print(f"  {what}: set")
            else:
                method, path, body = change
                api(path, method, body)
                print(f"  {what}: {'removed' if method == 'DELETE' else 'set'}")
    except SettingsError as error:
        print(f"cloudflare_settings: {error}", file=sys.stderr)
        return 1
    return left


if __name__ == "__main__":
    sys.exit(main())
