# The website

[firmwareliberator.com](https://firmwareliberator.com) is the repository's
README and docs as web pages, built with [Astro](https://astro.build) and
served by a Cloudflare Worker that holds nothing but the built files. The
pages are written once, as the repository's Markdown; this folder only turns
them into a site.

This page says how the pieces fit and what a person does by hand. The
specifics - a limit, a rule, a permission, a schedule - are written once,
beside the code that sets them, with the reason for each.

## The pieces

| Piece | What it is | Where it is written down |
|---|---|---|
| The site | The Markdown pages, and `/stats`, built into files and served by the Worker `firmware-liberator` | `src/`, `astro.config.mjs`, `public/`, `wrangler.jsonc` |
| The API | `api.firmwareliberator.com`: a Worker of its own, which takes the reports and readouts the tool sends | `api/` ([api/openapi.json](api/openapi.json) describes each path; [api/index.js](api/index.js) says how each is guarded) |
| The admin view | `admin.firmwareliberator.com`: the maintainer's view of what the API holds, behind Cloudflare Access | `admin/` |
| The zone and the account | DNS, redirects, rate-limiting, firewall and cache rules, the two buckets, the database, email, Access, Web Analytics | [tools/repo/cloudflare_settings.py](../tools/repo/cloudflare_settings.py) |
| The repository's side | The `website` environment, its secrets and the branch it takes | [tools/repo/github_settings.py](../tools/repo/github_settings.py) |
| Deploying and checking | The workflows that build, deploy and check all of the above | `.github/workflows/website.yml`, `cloudflare-settings.yml` |

The API is kept apart from the site so that nothing sent to it can affect
the site. How readouts are handled, and how to look after what the API holds,
is in [.github/readouts.md](../.github/readouts.md).

## Changing something

| To change | Edit | Then |
|---|---|---|
| A page | Its Markdown file | Push: the website workflow builds and deploys it |
| How pages are addressed, linked or described | `src/site.mjs` | `npm test`, push |
| A limit or a path of the API | `api/`, and `api/openapi.json` (below) | `npm test`, push; `apply` for the firewall rule, made from `openapi.json` |
| The API's database | A new file in `api/migrations/` (`api/database.js` says how one is written) | Run `apply`, then push |
| A DNS record, rule, bucket or other Cloudflare setting | `cloudflare_settings.py` | Run `apply` (below), then push |
| A secret or setting of the `website` environment | `github_settings.py` | `python3 tools/repo/github_settings.py apply`, or `... secret NAME` |
| A Cloudflare token's permissions | The dashboard, and the token's list in `cloudflare_settings.py` | Run the script's check |

### Changing the API

`api/openapi.json` describes the API (OpenAPI 3.1): each path, what it
takes, and every answer it gives. `test/openapi.test.mjs` holds the Worker to
it both ways - every answer it gives is described, every one described is
given - and the tool's own requests too; the firewall rule in front of the API
is made from its paths.

A path under `/v1/` keeps working for every version of the tool released
against it, so a change to one may only widen what it takes and narrow what
it answers (adding a field to an answer is fine: the tool ignores one it does
not know). A change that cannot goes under `/v2/`, beside `/v1/`, until no
tool still in use asks the old. `/confirm` and `/forget` are in no version:
they are links in emails, which must keep working.

Each description released is kept in `test/fixtures/openapi/`, named by its
`info.version` and never changed once out, and the test holds every later one
compatible with it. A release that changes the API raises `info.version`
and adds its description there, with its SHA-256 in the test's `WRITTEN`.

## Pages and addresses

Every Markdown file git tracks is a page, except those under `.github/`. A
page's address is its path without `.md`: `docs/emulator.md` is
`/docs/emulator`, a folder's `README.md` is the folder, and the README is
`/`. A link to another page goes to its address, and a link to anything else
in the repository goes to it on GitHub. `src/site.mjs` holds these rules, and
`test/` tests them.

Pages under `archive/` stay readable but are kept out of search results.
`public/llms.txt` is a short guide to the site for AI tools, written by hand:
add a page to it when the page matters to someone converting a device.
`tests/repo/test_site.py` holds its list of devices, and the wording that
names every maker and firmware, to what the tool has.

`/stats` is built from the public figures the API's nightly job writes
(`src/stats.mjs`), so the website workflow also builds daily, after them. To
build the page without the live figures, make some up:

    node scripts/sample-stats.mjs > /tmp/stats.json
    STATS_FILE=/tmp/stats.json npm run build

## Building it

Node 22 or newer:

    cd website
    npm ci --ignore-scripts
    npm test
    npm run build          # into dist/
    npm run check          # what was built, as search engines read it
    npx astro preview      # dist/ at http://localhost:4321

Packages come only from `package-lock.json`, by hash, with no install
scripts run. Dependabot moves them (`.github/dependabot.yml`), and the
dependabot-merge workflow merges its pull requests.

## Deploying it

`.github/workflows/website.yml` tests and builds the site for each pull
request. On each push to `main` it deploys the three Workers, each in a job
of its own so that no deploy holds up another, and then checks what it
deployed. It runs in the `website` environment, which only `main` can use.

### The Cloudflare tokens

Each token is made by hand in Cloudflare's dashboard and allowed only what
its job needs. `cloudflare_settings.py` lists what to give each, with what
needs it.

| Token | For | Kept | Its permissions |
|---|---|---|---|
| Deploy | The website workflow deploying the three Workers, and no other in the account | `website` secret `CLOUDFLARE_DEPLOY_TOKEN` | `DEPLOY_ZONE_PERMISSIONS`, `DEPLOY_WORKERS_PERMISSIONS` |
| Settings check | The cloudflare-settings workflow reading the settings and the API's health | `website` secret `CLOUDFLARE_READ_TOKEN` | `READ_TOKEN_PERMISSIONS` |
| Settings apply | You, changing a setting | Nowhere: made when needed, deleted after | `APPLY_TOKEN_PERMISSIONS` |
| Admin analytics | The admin view showing the month's use | `ADMIN_ANALYTICS_TOKEN` in `.env.infra`; `apply` sets it on the Worker | Account Analytics: Read alone |

The deploy token's permissions are checked with the apply token, never by
CI: reading one token means reading every token in the account. What each of
CI's tokens can see is checked instead, on each run, against a list of what
it must be refused. Set a secret with `python3 tools/repo/github_settings.py
secret <name>`, which asks for the value or takes it on stdin.

### Changing a Cloudflare setting

Settings are applied by hand, never by CI. They change rarely, and a CI token
that can only read cannot, if it leaks, change the DNS or a bucket.

1. Edit `cloudflare_settings.py`. A record or rule made in the dashboard is
   a difference that `apply` removes, so write it down there instead.
2. Make the apply token and put it in `CLOUDFLARE_API_TOKEN`, in the
   environment or `.env.infra` (`.env.infra.example` lists every name that
   file takes).
3. Run `python3 tools/repo/cloudflare_settings.py` to see what differs, and
   `... apply` to set it. `apply` says what is left to do by hand.
4. Delete the token, and push.

The cloudflare-settings workflow then checks the settings daily, and on each
push that changes the script. It fails on any difference, whether made in the
dashboard or written down and not applied. It cannot check the database, the
Workers' secrets, Access or Web Analytics, which its token is refused: the
script run by hand, with the apply token, checks those.

### The admin view

Cloudflare Access stands in front of the admin view, and the Worker checks
Access's signed token on every request itself, so a request that reached it
some other way is refused. `apply` makes Access's part and says each step
that is left to you. Two are always yours:

1. A Zero Trust organisation for the account (dashboard, Zero Trust, free
   plan).
2. A GitHub OAuth app for the sign-in, with the homepage and callback
   `apply` names. Its id and secret go in `.env.infra` as
   `ACCESS_GITHUB_CLIENT_ID` and `ACCESS_GITHUB_CLIENT_SECRET`.

The one address let in is `ACCESS_ADMIN_EMAIL` in `.env.infra`, or else
`CLOUDFLARE_EMAIL_DESTINATION`. `apply` then says the three values to put in
`admin/wrangler.jsonc`; until they are there the Worker lets no one in. CI
does not check Access, and each deploy checks only that the view turns away a
request that is not signed in.

### Adding a Worker

The deploy token can only be given a Worker that already exists, so a new
one is first uploaded from your computer:

1. Make a short-lived token: Account - Workers Scripts: Edit, Workers R2
   Storage: Edit, D1: Edit, and the zone permissions of the deploy and apply
   tokens. Put it in `CLOUDFLARE_API_TOKEN`.
2. Check the name is free (dashboard, Workers & Pages): an upload under a
   taken name replaces that Worker.
3. Run `apply` to make what the Worker binds to, as the upload fails on a
   binding to something that is not there. For the database it says the id
   to put in the Worker's `wrangler.jsonc`: commit that before the upload.
4. Upload it from `website/`: `npx wrangler deploy --config <its
   wrangler.jsonc>`. Or, to give the deploy token the Worker before it is
   ready, upload a placeholder under its name with the Workers API: a script
   answering 404, with no route, custom domain, `workers.dev` address or
   previews. The workflow's first deploy then replaces it.
5. Run `apply` again. It sets the Worker's secrets, which its
   `wrangler.jsonc` describes, and says what is left.
6. Check it answers (below).
7. Add it to the deploy token's Workers, in the dashboard and in
   `DEPLOY_WORKERS`, and give the website workflow a job that deploys it.
8. Delete the short-lived token.

### Stopping the API

Should something need looking at, the API can stop taking requests without a
change to the repository: Actions, Website, Run workflow, with **ingest**
off. Every path then answers 503, and the tool keeps what it would have sent
to send later. This lasts until the next deploy, which any push to `main`
makes. To keep it off longer, set `INGEST` to `"off"` in `api/wrangler.jsonc`
and push that.

### Checking a deploy

The API's deploy checks itself: its last step,
`python3 tools/repo/api_health.py live`, fails the job unless the new Worker
answers as one deployed with its database and newest migration does. By
hand:

```sh
curl -sI https://firmwareliberator.com/             # 200
curl -sI https://firmwareliberator.com/liberate.sh  # 302
curl -s -o /dev/null -w '%{http_code}\n' https://api.firmwareliberator.com/v1/readout  # 403: only a POST gets through
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://api.firmwareliberator.com/v1/reports  # 415: the Worker answers
curl -s -o /dev/null -w '%{http_code}\n' https://api.firmwareliberator.com/         # 403
curl -s https://stats.firmwareliberator.com/v1/stats.json | head -c 120   # after the first night
```
