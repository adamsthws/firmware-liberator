# The website

[firmwareliberator.com](https://firmwareliberator.com) is the repository's
README and docs as web pages, built with [Astro](https://astro.build) and
served by a Cloudflare Worker that holds nothing but the built files. The
pages are written once, as the repository's Markdown; this folder only turns
them into a site.

## What is here

| | |
|---|---|
| `src/site.mjs` | Which Markdown files are pages (`PAGES`: every one git tracks), the address of each, and how a link between them is rewritten |
| `src/content.config.mjs` | Those files as Astro's `pages` collection, read from the repository root |
| `src/pages/[...slug].astro` | One page per file, titled by its first heading and described by its first paragraph |
| `src/pages/404.astro` | What an address with no page answers |
| `src/layouts/Page.astro` | Every page's frame: GitHub's Markdown styling, dark unless the reader picks light, the GitHub button, Cloudflare Web Analytics (no cookies) |
| `public/` | Copied as they are: the icons and stylesheets, `robots.txt`, `llms.txt`, `_redirects` |
| `astro.config.mjs` | The build: Markdown through Astro's Sätteri with the link rewriting, pictures copied beside their pages, a sitemap |
| `test/` | Tests of the link rewriting, descriptions and `llms.txt`'s links (`npm test`, Node's own runner) |
| `wrangler.jsonc` | The Worker: the files in `dist/`, the 404 page, and both names pointed at it |

## Addresses

Every Markdown file git tracks is a page, but `.github/`'s templates. A page's
address is its path without `.md`: `docs/emulator.md` is
`/docs/emulator`, a folder's `README.md` is the folder (`/devices/sonoff-s61`),
and the README is `/`. A link to another page goes to its address; a link to
anything else in the repository - the tool, a config, `LICENSE` - goes to it
on GitHub; pictures under `docs/` are served beside the pages. The Worker
also answers `/docs/emulator.html` (by redirect), the form the old GitHub
Pages site's addresses took.

`public/_redirects` sends `/liberate.sh` and `/liberate.ps1` to the latest
release's, with a 302 so no browser keeps it past the next release.
`tests/repo/test_site.py` checks each is a file every release carries.

## Search engines

`robots.txt` lets every crawler in, and points at the sitemap. Pages under
`archive/` stay readable, but are marked `noindex` and left out of the
sitemap, so a search doesn't land someone on a superseded procedure; they
must not be blocked in `robots.txt`, or crawlers would never see the
`noindex`. `llms.txt` is a short guide to the site for AI tools, written by
hand: add a page to it when a page matters to someone converting a plug.
Its list of devices follows the tool: `tests/repo/test_site.py` fails until
each device the tool knows has its line there, in the tool's order, with its
status from `devices/status.json` and the firmware it converts to. The same
test holds the front page's title and description, `llms.txt`'s summary, the
README's opening and the repository's description on GitHub to naming every
maker and firmware the tool has.

`checks/built.mjs` checks what the build made: the sitemap lists every page
but the retired ones, each page names its own canonical address and has a
description, only the retired pages and the 404 are `noindex`, and
`robots.txt` names the sitemap that was built. Run it after a build with
`npm run check`; the website workflow does, before it deploys.

## Building it

Node 22 or newer:

    cd website
    npm ci --ignore-scripts
    npm test
    npm run build          # into dist/
    npx astro preview      # dist/ at http://localhost:4321

Packages come only from `package-lock.json`, by hash, with no install
scripts run. Dependabot moves them, a week after each release
(`.github/dependabot.yml`), and the dependabot-merge workflow merges its pull
request once it has been open a week with its checks passed.

## Deploying it

`.github/workflows/website.yml` tests and builds it for each pull request,
and on each push to `main` deploys both Workers with wrangler: the site
(`website/wrangler.jsonc`) and the readouts API (`website/api/wrangler.jsonc`).
It runs in the `website` environment, which only `main` can use
(`tools/repo/github_settings.py`).

Three Cloudflare API tokens are in play, each allowed only what its job needs:

| Token | Kept | Permissions |
| --- | --- | --- |
| Deploy | `website` secret `CLOUDFLARE_DEPLOY_TOKEN` | Individual Workers - `firmware-liberator`, `firmware-liberator-api`: Editor. Zone `firmwareliberator.com` - Workers Routes: Write, DNS: Write (the custom domains), Zone: Read |
| Settings check | `website` secret `CLOUDFLARE_READ_TOKEN` | Zone `firmwareliberator.com` - Zone: Read, Zone Settings: Read, DNS: Read, Dynamic URL Redirects: Read, Zone WAF: Read, Cache Settings: Read (the cache rules), Email Routing Rules: Read, Analytics: Read (the mail check). Account - Workers R2 Storage: Read, Account Analytics: Read (the usage check), Email Routing Addresses: Read, Email Sending: Read (the mail check's suppression list) |
| Settings apply | nowhere: made when needed, deleted after | Zone `firmwareliberator.com` - Zone: Read, Zone Settings: Edit, DNS: Edit, Dynamic URL Redirects: Edit, Zone WAF: Edit, Cache Settings: Edit (the cache rules), Email Routing Rules: Edit. Account - Workers R2 Storage: Edit, D1: Edit, Email Routing Addresses: Edit, Email Sending: Edit (onboarding the domain for sending), Account API Tokens: Read, Workers Scripts: Edit (to check the deploy token, and set the API Worker's secrets) |

The deploy token reaches its two Workers and no other in the account. Its
permissions are written down in `tools/repo/cloudflare_settings.py` and
checked by hand, with the apply token: reading one token means reading every
token in the account, other sites' too, which CI's token is not allowed. What
it can see is checked on each deploy, by the workflow's `deploy-token` job
with the token itself, after both deploys so a mistake there holds up
neither: `firmwareliberator.com` and this account alone, its two Workers, and
none of what `cloudflare_settings.py` lists as refused. The
environment's secrets are written down in `tools/repo/github_settings.py`,
whose check fails on one missing; set one with `python3
tools/repo/github_settings.py secret <name>`, which asks for the value or
takes it on stdin.

A domain with DNS records of its own stops a deploy (Cloudflare's code
100117): delete them, and wrangler makes the Worker's.

### Adding a Worker

The deploy token can only be given a Worker that already exists, so a new
one is first uploaded from your computer:

1. Make a short-lived token: Account - Workers Scripts: Edit, Workers R2
   Storage: Edit, D1: Edit, and the zone permissions of the deploy and apply
   tokens. Put it in `CLOUDFLARE_API_TOKEN`.
2. Check the name is free: the account's Workers (dashboard, Workers &
   Pages) hold none by it. An upload under a taken name replaces that
   Worker.
3. Make what it binds to first: `python3 tools/repo/cloudflare_settings.py
   apply` makes the bucket and the database (the upload fails on a binding to
   one that is not there), and says the database's id to put in the API
   Worker's `wrangler.jsonc`: commit that before the upload.
4. Upload it from `website/`: `npx wrangler deploy --config <its
   wrangler.jsonc>`.
5. For the API Worker, run `apply` again: it sets its secrets
   (`website/api/wrangler.jsonc` says what each is) - SENDER_KEY made afresh,
   DEVICE_HASH_KEY_PERMANENT from `API_DEVICE_HASH_KEY_PERMANENT` in `.env.infra` - and says what is left.
6. Check it answers (below).
7. Add it to the deploy token's Individual Workers, and give the website
   workflow a job deploying it.
8. Delete the short-lived token.

### Stopping the API

Should something need looking at, the API can stop taking requests without
a change to the repository: Actions, Website, Run workflow, with **ingest**
off. Every path then answers 503, and the tool keeps what it would have sent
to send later. It lasts until the next deploy, which any push to `main`
makes; to keep it off longer, set `INGEST` to `"off"` in
`website/api/wrangler.jsonc` and push that.

### Checking a deploy

The API's deploy checks itself: its last step, `python3
tools/repo/api_health.py live`, asks the new Worker what only one deployed
with its database and newest migration answers rightly, through the
firewall, and fails the job if not - after a deploy made before `apply` ran
a new migration, say, it says to run it. It needs no token, and counts
nothing. By hand:

```sh
curl -sI https://firmwareliberator.com/             # 200
curl -sI https://firmwareliberator.com/liberate.sh  # 302
curl -s -o /dev/null -w '%{http_code}\n' https://api.firmwareliberator.com/readout  # 403: only a POST gets through
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.firmwareliberator.com/reports  # 415: the Worker answers
curl -s -o /dev/null -w '%{http_code}\n' https://api.firmwareliberator.com/         # 403
curl -s https://stats.firmwareliberator.com/v1/stats.json | head -c 120   # after the first night
```

## The zone's settings

What the zone and the account are set to beyond the Workers - `www.` sent
on to the bare domain, HTTPS only, TLS 1.2 or newer, email addresses in
pages hidden from scrapers, the API's rate-limiting rule and its firewall
rule (only a POST to one of its paths reaches the Worker), the readouts
bucket (kept for good: no lifecycle rule), the public figures' bucket at
`stats.firmwareliberator.com` with its cache rule, Email Routing, DNSSEC,
and every DNS record but the Workers' and the figures' domain's, the CAA
records letting only Cloudflare's certificate authorities issue for the
domain among them - is written down, with why, in
`tools/repo/cloudflare_settings.py`. A DNS record added in the dashboard is
a difference, deleted by `apply`: write it down there instead.

The API's D1 database is made there too, and brought up to date: `apply`
runs each file in `website/api/migrations/` it has not run yet, in order
(`website/api/database.js` says how one is written). The apply token needs
D1: Edit for that. CI never touches it: D1 permissions reach every database
in the account, so the read token is refused them, and the check it runs
says the database was not checked.

Each address of the domain in `CLOUDFLARE_EMAIL_FORWARD`, named by the
part before the @ (`contact`, say, separated by commas), is forwarded to
`CLOUDFLARE_EMAIL_DESTINATION`, the account's verified destination address;
mail to any other is dropped. Both are private, kept out of the repository
where scrapers would find them: in `.env.infra`, and in the `website` secrets of
those names, which `python3 tools/repo/github_settings.py apply` sets from
`.env.infra` each time it runs. The check never prints them.

The settings change rarely, so they are set by hand, never by CI: a leaked CI token
cannot change DNS-facing settings or the bucket. To change one, edit the
script, then with the apply token in `CLOUDFLARE_API_TOKEN` (the environment
or `.env.infra`) run `python3 tools/repo/cloudflare_settings.py` to see what
differs and `... apply` to set it; delete the token, and push.

The cloudflare-settings workflow checks them, with the read token, daily
and on each push to `main` that changes the script, failing on any
difference: one made in the dashboard, or one written down and not applied.
It runs `cloudflare_settings.py ci`, which also fails if the read token can
see a zone besides `firmwareliberator.com`, or the account's API tokens: a
token edited too wide is found by what it can read.

Crawlers, AI ones included, may read `/stats` and its figures: cached, they
cost nothing. Should that change, one more firewall rule in
`cloudflare_settings.py` would block Cloudflare's verified AI crawlers
there - `(cf.verified_bot_category eq "AI Crawler" and (http.request.uri.path
eq "/stats" or http.host eq "stats.firmwareliberator.com"))` - once
Cloudflare's documentation confirms the free plan allows that field.
