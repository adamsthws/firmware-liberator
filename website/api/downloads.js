// The tool's downloads as GitHub counts them, read each night (index.js
// scheduled) and kept by day (download_days), so the admin view and /stats can
// chart them beside the devices converted. GitHub gives only each release
// file's count so far, never by day: a day's row is what it said in the night
// after it, as the day's figures are made then (nightly.js), and a night it
// could not be read gives none, the chart going on from the last.
// - DOWNLOADS are the tool itself, summed over its releases (tags v<version>)
//   into one total: liberate.sh and liberate.ps1 fetch one for each new
//   version, as does someone downloading it from a release's page. Which
//   system a person is on is told by their reports, not by the file they
//   fetched: someone on a Mac may download the zip. The README's badge counts
//   every file of every release, the version file the one-liners read on
//   every run and the emulators' sources among them, so it says more.
// - Read with no token, as GitHub answers anyone 60 requests an hour from an
//   address: a Worker shares its addresses, so a night may be refused, and is
//   told so (refreshDownloads' answer).
import { ready } from "./database.js";

// github_settings.REPOSITORY's copy (tests/repo/test_site.py holds them alike).
export const REPOSITORY = "adamsthws/firmware-liberator";
// The release workflow's archives of the tool.
export const DOWNLOADS = ["autoflash.tar.gz", "autoflash.zip"];
const PER_PAGE = 100;
// Pages of releases read at most a night: a thousand releases.
export const RELEASE_PAGES = 10;
const TOOL_TAG = /^v\d+\.\d+\.\d+$/;

// {downloads} over the tool's releases, or {problem}.
export async function readDownloads(fetcher = fetch) {
  let downloads = 0;
  try {
    for (let page = 1; page <= RELEASE_PAGES; page += 1) {
      const response = await fetcher(`https://api.github.com/repos/${REPOSITORY}/releases?per_page=${PER_PAGE}&page=${page}`, {
        // GitHub refuses a request with no User-Agent.
        headers: { Accept: "application/vnd.github+json", "User-Agent": "firmware-liberator-api" },
        signal: AbortSignal.timeout(20_000),
      });
      if (!response.ok) {
        return { problem: `HTTP ${response.status}` };
      }
      const releases = await response.json();
      if (!Array.isArray(releases)) {
        return { problem: "not a list of releases" };
      }
      for (const release of releases) {
        if (!TOOL_TAG.test(release?.tag_name ?? "") || release.draft) {
          continue;
        }
        for (const asset of release.assets ?? []) {
          if (DOWNLOADS.includes(asset?.name) && Number.isInteger(asset.download_count)) {
            downloads += asset.download_count;
          }
        }
      }
      if (releases.length < PER_PAGE) {
        return { downloads };
      }
    }
    return { problem: `more than ${RELEASE_PAGES} pages of releases` };
  } catch (error) {
    return { problem: String(error?.message ?? error).slice(0, 200) };
  }
}

// Tonight's count kept for the day just ended: "taken", or why not. The
// nightly cron's, before the figures are made (index.js scheduled).
export async function refreshDownloads(env, now = new Date(), fetcher = fetch) {
  if (!(await ready(env.DB))) {
    return "the database does not have every migration yet";
  }
  const got = await readDownloads(fetcher);
  if (got.downloads === undefined) {
    return got.problem;
  }
  const day = new Date(Date.parse(`${now.toISOString().slice(0, 10)}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
  await env.DB.prepare("INSERT OR REPLACE INTO download_days (day, downloads) VALUES (?, ?)").bind(day, got.downloads).run();
  return "taken";
}

// Each day read, oldest first, from `since` (a day): its downloads, and the
// devices converted by then, of the devices `where` keeps.
// `all` runs a query (snapshot.js's, the admin view's).
export async function downloadDays(all, since, where) {
  const days = await all("SELECT day, downloads FROM download_days WHERE day >= ? ORDER BY day", since);
  if (!days.length) {
    return [];
  }
  const converted = await all(`SELECT converted_day AS day, count(*) AS n FROM devices
    WHERE converted_day IS NOT NULL AND converted_day <= ? AND ${where} GROUP BY converted_day ORDER BY converted_day`,
  days.at(-1).day);
  let at = 0;
  let sum = 0;
  return days.map((day) => {
    for (; at < converted.length && converted[at].day <= day.day; at += 1) {
      sum += converted[at].n;
    }
    return { day: day.day, downloads: day.downloads, converted: sum };
  });
}
