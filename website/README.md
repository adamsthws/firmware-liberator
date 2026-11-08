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
| Deploy | `website` secret `CLOUDFLARE_API_TOKEN` | Individual Workers - `firmware-liberator`, `firmware-liberator-api`: Editor. Zone `firmwareliberator.com` - Workers Routes: Write, DNS: Write (the custom domains), Zone: Read |
| Settings check | `website` secret `CLOUDFLARE_READ_TOKEN` | Zone `firmwareliberator.com` - Zone: Read, Zone Settings: Read, Dynamic URL Redirects: Read, Zone WAF: Read. Account - Workers R2 Storage: Read |
| Settings apply | nowhere: made when needed, deleted after | Zone `firmwareliberator.com` - Zone: Read, Zone Settings: Edit, Dynamic URL Redirects: Edit, Zone WAF: Edit. Account - Workers R2 Storage: Edit |

The deploy token reaches its two Workers and no other in the account. Set a
secret with `gh secret set <name> --env website -R adamsthws/firmware-liberator`,
which asks for the value.

A domain with DNS records of its own stops a deploy (Cloudflare's code
100117): delete them, and wrangler makes the Worker's.

### Adding a Worker

The deploy token can only be given a Worker that already exists, so a new
one is first uploaded from your computer:

1. Make a short-lived token: Account - Workers Scripts: Edit, Workers R2
   Storage: Edit, and the zone permissions of the deploy and apply tokens.
   Put it in `CLOUDFLARE_API_TOKEN`.
2. Check the name is free: the account's Workers (dashboard, Workers &
   Pages) hold none by it. An upload under a taken name replaces that
   Worker.
3. Make what it binds to first: `python3 tools/repo/cloudflare_settings.py
   apply` makes the bucket (the upload fails on a binding to a bucket that
   is not there).
4. Upload it from `website/`: `npx wrangler deploy --config <its
   wrangler.jsonc>`.
5. Check it answers (below).
6. Add it to the deploy token's Individual Workers, and give the website
   workflow a job deploying it.
7. Delete the short-lived token.

### Checking a deploy

```sh
curl -sI https://firmwareliberator.com/             # 200
curl -sI https://firmwareliberator.com/liberate.sh  # 302
curl -s -o /dev/null -w '%{http_code}\n' https://api.firmwareliberator.com/readout  # 405: only POST
curl -s -o /dev/null -w '%{http_code}\n' https://api.firmwareliberator.com/         # 404
```

## The zone's settings

What the zone and the account are set to beyond the Workers - `www.` sent
on to the bare domain, HTTPS only, TLS 1.2 or newer, the API's
rate-limiting rule, the readouts bucket and its 30-day lifecycle rule - is
written down, with why, in `tools/repo/cloudflare_settings.py`.

They change rarely, so they are set by hand, never by CI: a leaked CI token
cannot change DNS-facing settings or the bucket. To change one, edit the
script, then with the apply token in `CLOUDFLARE_API_TOKEN` (the environment
or `.env`) run `python3 tools/repo/cloudflare_settings.py` to see what
differs and `... apply` to set it; delete the token, and push.

The cloudflare-settings workflow checks them, with the read token, weekly
and on each push to `main` that changes the script, failing on any
difference: one made in the dashboard, or one written down and not applied.
