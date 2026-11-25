// admin.firmwareliberator.com: the maintainer's view of what the API holds,
// live from its database on each load - unlike /stats, which is the public
// figures, a day old. Behind Cloudflare Access (access.js checks its token on
// every request); the only changes it makes are a held report released or
// deleted, readouts nearly agreeing approved, a link to review approved (its
// file then fetched) or rejected, and the maintainer's switches set, each a
// POST from this view itself; and the suites' emulator runs, taken
// under SCOPE with Access's service token alone (emulator.js). Nothing in it
// is cached, framed or indexed, and it runs no script but its own two: the
// theme's and the copy button's.
import { release as giveBack } from "../api/counters.js";
import { downloadDays, DOWNLOADS } from "../api/downloads.js";
import { AGREEMENT_SETTING, DAILY_BYTES, DAILY_COUNT, MIN_NETWORKS, NOTIFY_DAILY, PER_KEY, READOUT_ID, STAGES,
  TOTAL_BYTES } from "../api/index.js";
import { CLOUD_ASNS } from "../api/networks.js";
import { COUNTER_DAYS, NAMED } from "../api/nightly.js";
import { approveLink, everyLink, fetchedFrom, fetchesOn, FIRST_SIGHT_DAILY, firstSightsOn, linkFigures, linksNotKeptOn,
  linksToReview, OFFER_FETCHES_DAILY, PEEK_BYTES, PENDING_LINK_DAYS, PENDING_LINKS_MOST, PENDING_LINKS_PER_SENDER,
  PENDING_LINKS_RENTED,
  rejectLink, sentAs, unfetched } from "../api/offered.js";
import { PENDING } from "../api/pending.js";
import { monthsTo, popularity, SERIES } from "../api/popularity.js";
import { DEVICE_DAILY, ID, REPORTS_DAILY } from "../api/reports.js";
import schema from "../api/report-schema.json" with { type: "json" };
import { SITE, THEME_SCRIPT, page as shellPage } from "../src/shell.mjs";
import { verified } from "./access.js";
import { emulatorSections, gatherEmulator, KINDS, SCOPE, setCause, upload } from "./emulator.js";
import { bytes, columns, copyField, esc, FULL, lineChart, lines, meter, number, STYLE, table, tile, trends } from "./render.js";

export const ORIGIN = "https://admin.firmwareliberator.com";
// What Workers Paid includes a month, and R2 free (tools/repo/api_health.py's
// INCLUDED; a test holds the two alike).
export const INCLUDED = {
  "Workers requests this month": 10_000_000,
  "D1 rows read this month": 25_000_000_000,
  "D1 rows written this month": 50_000_000,
  "R2 storage, bytes": 10 * 1000 ** 3,
};
const DAY = 86_400_000;
const dayOf = (date) => date.toISOString().slice(0, 10);

const HEADERS = {
  "Content-Type": "text/html; charset=utf-8",
  "Cache-Control": "no-store",
  // The site's stylesheets and icons (src/shell.mjs), but no script but the
  // theme's and the copy button's, served here: a change to the site can't
  // run code in this view.
  "Content-Security-Policy": `default-src 'none'; style-src 'unsafe-inline' ${SITE}; script-src 'self'; `
    + `img-src data: ${SITE}; form-action 'self'; frame-ancestors 'none'; base-uri 'none'`,
  "X-Robots-Tag": "noindex, nofollow",
  // Nothing of this view's addresses to another site; and not no-referrer,
  // under which a browser sends its own forms' POSTs with Origin: null, so
  // each would be refused as not from this view (ownForm).
  "Referrer-Policy": "same-origin",
  "X-Content-Type-Options": "nosniff",
};
const reply = (status, body, headers = {}) => new Response(body, { status, headers: { ...HEADERS, ...headers } });

// The copy buttons' script (render.js copyField), served from here as
// /assets/copy.js: it copies the field a button names, by the page's own id,
// and reads nothing else of the page.
export const COPY_SCRIPT = `document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-copy]");
  const field = button && document.getElementById(button.dataset.copy);
  if (!field) return;
  field.select();
  navigator.clipboard.writeText(field.value).then(() => { button.textContent = "Copied"; }, () => {});
});
`;

export default {
  fetch: (request, env) => handle(request, env),
};

// A request, its Access token checked first: `now` and `fetcher` (Access's
// keys) as the Worker has them, but in the tests, and `files` the fetch an
// approved link's file is fetched with.
export async function handle(request, env, now = Date.now(), fetcher = fetch, files = fetch) {
  const url = new URL(request.url);
  if (url.pathname === SCOPE || url.pathname.startsWith(`${SCOPE}/`)) {
    return suites(request, url, env, now, fetcher);
  }
  const who = await verified(request, env, now, fetcher);
  if (!who) {
    return reply(403, "Forbidden: sign in through Cloudflare Access.", { "Content-Type": "text/plain" });
  }
  if (url.pathname === "/assets/theme.js" && request.method === "GET") {
    return new Response(THEME_SCRIPT, { headers: { ...HEADERS, "Content-Type": "text/javascript; charset=utf-8" } });
  }
  if (url.pathname === "/assets/copy.js" && request.method === "GET") {
    return new Response(COPY_SCRIPT, { headers: { ...HEADERS, "Content-Type": "text/javascript; charset=utf-8" } });
  }
  if (url.pathname === "/" && request.method === "GET") {
    return reply(200, await page(env, who, new Date(now), fetcher, url.searchParams.get("live") === "1"));
  }
  if (url.pathname === "/held" && request.method === "POST") {
    return held(request, env);
  }
  if (url.pathname === "/settings" && request.method === "POST") {
    return setting(request, env);
  }
  if (url.pathname === "/approve" && request.method === "POST") {
    return approve(request, env);
  }
  if (url.pathname === "/links" && request.method === "POST") {
    return review(request, env, new Date(now), files);
  }
  if (url.pathname === "/emulator-cause" && request.method === "POST") {
    return cause(request, env);
  }
  return reply(404, "Not found.", { "Content-Type": "text/plain" });
}

// What the suites send, under SCOPE: only with a JWT Access made for their
// application (ACCESS_UPLOAD_AUD), whose one policy lets in the suites'
// service token - which carries no one's address, as a person's sign-in does.
async function suites(request, url, env, now, fetcher) {
  const claims = await verified(request, env, now, fetcher, env.ACCESS_UPLOAD_AUD);
  if (!claims || claims.email || typeof claims.common_name !== "string") {
    return reply(403, "Forbidden: only the suites' service token.", { "Content-Type": "text/plain" });
  }
  if (!Object.hasOwn(KINDS, url.pathname)) {
    return reply(404, "Not found.", { "Content-Type": "text/plain" });
  }
  if (request.method !== "POST") {
    return reply(405, "POST runs.", { "Content-Type": "text/plain", Allow: "POST" });
  }
  return upload(request, env, now, url.pathname);
}

// A failure's cause marked: only from a form on this view.
async function cause(request, env) {
  if (!ownForm(request)) {
    return notOwn();
  }
  if (!(await setCause(await request.formData(), env))) {
    return reply(400, "Not a run and a cause.", { "Content-Type": "text/plain" });
  }
  return new Response(null, { status: 303, headers: { Location: "/#emulator-failures", "Cache-Control": "no-store" } });
}

// Whether a POST came from a form on this view itself.
const ownForm = (request) => request.headers.get("Origin") === ORIGIN
  && (request.headers.get("Sec-Fetch-Site") ?? "same-origin") === "same-origin";
const notOwn = () => reply(403, "Only from the admin view itself.", { "Content-Type": "text/plain" });

// The switches this view sets, each "on" or "off": on when never set.
export const SWITCHES = { [AGREEMENT_SETTING]: "Email me when readouts come to agree" };

// A switch set: only from a form on this view.
async function setting(request, env) {
  if (!ownForm(request)) {
    return notOwn();
  }
  const form = await request.formData();
  const name = String(form.get("name") ?? "");
  const value = String(form.get("value") ?? "");
  if (!Object.hasOwn(SWITCHES, name) || !["on", "off"].includes(value)) {
    return reply(400, "Not a switch and on or off.", { "Content-Type": "text/plain" });
  }
  await env.DB.prepare("INSERT INTO settings (name, value) VALUES (?1, ?2) ON CONFLICT (name) DO UPDATE SET value = ?2")
    .bind(name, value).run();
  return new Response(null, { status: 303, headers: { Location: "/#switches", "Cache-Control": "no-store" } });
}

// Readouts alike, PER_KEY or more of a key and stage, waiting for review only
// because they came from fewer than MIN_NETWORKS networks (api/index.js).
export const NEARLY = `SELECT key, stage, fingerprint, max(offered) AS offered, count(*) AS n,
    count(DISTINCT network) AS networks,
    min(received) AS first, max(received) AS last
  FROM readouts LEFT JOIN readout_networks ON readout_networks.id = readouts.id
  WHERE review = 'pending' GROUP BY key, stage, fingerprint HAVING count(*) >= ${PER_KEY} ORDER BY last DESC`;

// Keys whose whole readouts taken as right a later one disagrees with,
// unsettled (api/index.js CHECK_DAYS).
export const DISPUTED = `SELECT key, stage, sum(review IN ('quorum', 'approved')) AS agreed,
    sum(checked = 1 AND review = 'pending') AS disagree, max(received) AS last
  FROM readouts WHERE stage = 'whole' GROUP BY key, stage HAVING disagree > 0 ORDER BY last DESC`;

// Readouts nearly agreeing approved, as readouts.py approve does each: taken
// as right, and no longer held to the room for those waiting for review.
// Only from a form on this view.
async function approve(request, env) {
  if (!ownForm(request)) {
    return notOwn();
  }
  const form = await request.formData();
  const [key, stage, fingerprint] = ["key", "stage", "fingerprint"].map((name) => String(form.get(name) ?? ""));
  if (!/^[A-Za-z0-9._|-]{1,130}$/.test(key) || !STAGES.includes(stage) || !/^[0-9a-f]{64}$/.test(fingerprint)) {
    return reply(400, "Not readouts' key, stage and fingerprint.", { "Content-Type": "text/plain" });
  }
  const { results } = await env.DB.prepare(`UPDATE readouts SET review = 'approved' WHERE key = ?1 AND stage = ?2
    AND fingerprint = ?3 AND review = 'pending'
    AND (SELECT count(*) FROM readouts WHERE key = ?1 AND stage = ?2 AND fingerprint = ?3 AND review = 'pending') >= ?4
    RETURNING bytes`).bind(key, stage, fingerprint, PER_KEY).all();
  if (results.length) {
    await giveBack(env.DB, PENDING, results.reduce((total, row) => total + row.bytes, 0), results.length);
  }
  return new Response(null, { status: 303, headers: { Location: "/#nearly", "Cache-Control": "no-store" } });
}

// A link to review approved, its file then fetched by it (api/offered.js
// approveLink), or rejected, never to be fetched: wherever it was sent, by
// one readout's link of the file. Only from a form on this view.
async function review(request, env, now, files) {
  if (!ownForm(request)) {
    return notOwn();
  }
  const form = await request.formData();
  const [readout, sha, action] = ["readout", "sha", "action"].map((name) => String(form.get(name) ?? ""));
  if (!READOUT_ID.test(readout) || !/^[0-9a-f]{64}$/.test(sha) || !["approve", "reject"].includes(action)) {
    return reply(400, "Not a readout's link of a file, and approve or reject.", { "Content-Type": "text/plain" });
  }
  if (action === "approve") {
    await approveLink(env, readout, sha, now, { totalBytes: TOTAL_BYTES, fetcher: files });
  } else {
    await rejectLink(env.DB, readout, sha);
  }
  return new Response(null, { status: 303, headers: { Location: "/#review", "Cache-Control": "no-store" } });
}

// A held report released or deleted, or every one held of one device on one
// day for one reason released: only from a form on this view.
async function held(request, env) {
  if (!ownForm(request)) {
    return notOwn();
  }
  const form = await request.formData();
  const field = (name) => String(form.get(name) ?? "");
  const action = field("action");
  let writes;
  if (action === "release-all" && /^\d{4}-\d\d-\d\d$/.test(field("day")) && SHORT_FIELD.test(field("why"))
      && SHORT_FIELD.test(field("named"))) {
    writes = releaseAll(env.DB, field("day"), field("why"), field("named"));
  } else if (ID.test(field("id")) && ["release", "delete"].includes(action)) {
    writes = action === "release" ? release(env.DB, field("id")) : remove(env.DB, field("id"));
  } else {
    return reply(400, "Not a held report and an action.", { "Content-Type": "text/plain" });
  }
  await env.DB.batch(writes);
  return new Response(null, { status: 303, headers: { Location: "/#held", "Cache-Control": "no-store" } });
}
const SHORT_FIELD = /^[\x20-\x7e]{1,200}$/;

// Released: counted again, its day folded afresh the next night, and marked
// released so that night's spike check does not hold it again (nightly.js
// spikes) - the reports `which` selects of those held, a run's or a
// readout's, in four statements however many.
const releasing = (db, which, ...values) => [
  db.prepare(`DELETE FROM folded WHERE day IN (SELECT day FROM held_reports WHERE id IN (${which}))`).bind(...values),
  db.prepare(`UPDATE reports SET quarantined = 0, released = 1 WHERE id IN (${which})`).bind(...values),
  db.prepare(`UPDATE readout_reports SET quarantined = 0 WHERE id IN (${which})`).bind(...values),
  db.prepare(`DELETE FROM held_reports WHERE id IN (${which})`).bind(...values),
];
export const release = (db, id) => releasing(db, "SELECT id FROM held_reports WHERE id = ?1", id);
// Every one held for `why` on `day` of one device, as the nightly job counts
// a spike's (NAMED: its kind, else its model).
export const releaseAll = (db, day, why, named) => releasing(db, `SELECT held_reports.id FROM held_reports
  JOIN reports ON reports.id = held_reports.id WHERE held_reports.day = ?1 AND why = ?2 AND ${NAMED} = ?3`,
day, why, named);

// Deleted, with its device's record where no other report of it is left.
export const remove = (db, id) => [
  db.prepare(`DELETE FROM devices WHERE device_hash = (SELECT device_hash FROM reports WHERE id = ?1
    AND id IN (SELECT id FROM held_reports)) AND NOT EXISTS (SELECT 1 FROM reports
    WHERE device_hash = devices.device_hash AND id != ?1)`).bind(id),
  db.prepare("DELETE FROM report_stages WHERE id = ? AND id IN (SELECT id FROM held_reports)").bind(id),
  db.prepare("DELETE FROM report_offers WHERE id = ? AND id IN (SELECT id FROM held_reports)").bind(id),
  db.prepare("DELETE FROM identity_checks WHERE id = ? AND id IN (SELECT id FROM held_reports)").bind(id),
  db.prepare("DELETE FROM reports WHERE id = ? AND id IN (SELECT id FROM held_reports)").bind(id),
  db.prepare("DELETE FROM readout_reports WHERE id = ? AND id IN (SELECT id FROM held_reports)").bind(id),
  db.prepare("DELETE FROM held_reports WHERE id = ?").bind(id),
];

// As tools/repo/api_health.py asks it.
const USAGE_QUERY = `query Usage($account: string!, $first: Date!, $last: Date!, $from: Time!, $to: Time!) {
  viewer { accounts(filter: {accountTag: $account}) {
    workers: workersInvocationsAdaptive(limit: 10000, filter: {date_geq: $first, date_leq: $last}) { sum { requests } }
    d1: d1AnalyticsAdaptiveGroups(limit: 10000, filter: {date_geq: $first, date_leq: $last}) { sum { rowsRead rowsWritten } }
    r2: r2StorageAdaptiveGroups(limit: 10000, filter: {datetime_geq: $from, datetime_lt: $to}) {
      max { payloadSize metadataSize } dimensions { bucketName } }
  } } }`;

// This month's use, from Cloudflare's analytics (ANALYTICS_TOKEN, a read-only
// token, and ACCOUNT_ID): {measure: value}, or a sentence why there is none.
export async function usage(env, now, fetcher = fetch) {
  if (!env.ANALYTICS_TOKEN || !env.ACCOUNT_ID) {
    return "Set the ANALYTICS_TOKEN secret (cloudflare_settings.py apply does) to see it here.";
  }
  const first = `${dayOf(now).slice(0, 8)}01`;
  try {
    const response = await fetcher("https://api.cloudflare.com/client/v4/graphql", {
      method: "POST", headers: { Authorization: `Bearer ${env.ANALYTICS_TOKEN}`, "Content-Type": "application/json" },
      body: JSON.stringify({ query: USAGE_QUERY, variables: { account: env.ACCOUNT_ID, first, last: dayOf(now),
        from: `${dayOf(new Date(now.getTime() - DAY))}T00:00:00Z`, to: `${dayOf(now)}T00:00:00Z` } }),
    });
    const answer = await response.json();
    if (answer.errors?.length) {
      return `Cloudflare's analytics: ${answer.errors.map((error) => error.message).join("; ")}`;
    }
    const account = answer.data.viewer.accounts[0] ?? {};
    const sum = (rows, key) => (rows ?? []).reduce((total, row) => total + (row.sum?.[key] ?? 0), 0);
    const largest = new Map();
    for (const row of account.r2 ?? []) {
      const size = (row.max?.payloadSize ?? 0) + (row.max?.metadataSize ?? 0);
      largest.set(row.dimensions?.bucketName, Math.max(largest.get(row.dimensions?.bucketName) ?? 0, size));
    }
    return {
      "Workers requests this month": sum(account.workers, "requests"),
      "D1 rows read this month": sum(account.d1, "rowsRead"),
      "D1 rows written this month": sum(account.d1, "rowsWritten"),
      "R2 storage, bytes": [...largest.values()].reduce((total, size) => total + size, 0),
    };
  } catch (error) {
    return `Cloudflare's analytics could not be read: ${error.message}`;
  }
}

// Your users' runs, devices seen and devices converted, in all and by the
// system they ran the tool on (the reports' os.family; downloads cannot say,
// as someone on a Mac may download the zip): your own devices and held
// reports left out. A device is seen as the devices table and /stats count
// it, so not where its every run was stopped by the person; one run from two
// systems is in each, once in all.
const COUNTED = `SELECT count(*) AS runs,
    count(DISTINCT CASE WHEN device_hash IN (SELECT device_hash FROM devices) THEN device_hash END) AS seen,
    count(DISTINCT CASE WHEN result = 'converted' THEN device_hash END) AS converted
  FROM reports WHERE tag = 'user' AND quarantined = 0
    AND (device_hash IS NULL OR device_hash NOT IN (SELECT device_hash FROM devices WHERE dev = 1))`;
// How the runs reported in the last fortnight ended: each part's name, the
// reports' results it counts (report-schema.json's run results, each once)
// and its colour, as the emulator runs' chart colours passed and failed.
// Updated and put-back runs finished as a conversion does.
export const OUTCOMES = [["converted", ["converted", "updated", "put-back"], "series-1"], ["failed", ["failed"], "series-2"],
  ["crashed", ["crashed"], "critical"], ["cancelled", ["cancelled"], "muted"]];
export const SYSTEMS = `SELECT * FROM (SELECT 'all' AS os, * FROM (${COUNTED})
    UNION ALL SELECT * FROM (${COUNTED.replace("SELECT", "SELECT os_family AS os,")} GROUP BY os_family))
  ORDER BY os = 'all' DESC, runs DESC, os`;
export const SYSTEM_NAMES = { all: "All systems", windows: "Windows", macos: "macOS", linux: "Linux" };

// A report's device kind, as a person calls it (report-schema.json); "" for none.
const deviceName = (row) => schema.deviceNames[row.device] ?? row.device ?? "";
// Where a run or readout stopped: its step, how, and the question it was
// stopped at, where each is known.
const stoppedAt = (row) => [row.step, row.error, row.question && `at ${row.question}`].filter(Boolean).join(" ");
// What the check of Sonoff's firmware found before a switch off and on was
// asked for, shown beside where it stopped: "" where none ran.
const STOCK_CHECK = ["Sonoff's firmware", (row) => row.stock_check ?? ""];

// Everything the view shows, in a fixed few queries.
export async function gather(env, now) {
  const all = async (sql, ...values) => (await env.DB.prepare(sql).bind(...values).all()).results;
  const today = dayOf(now);
  const since = (days) => dayOf(new Date(now.getTime() - days * DAY));
  const names = [`reports:${today}`, `readouts:${today}`, `notify:${today}`, `mail-sent:${today}`, "readouts:all",
    fetchesOn(today), firstSightsOn(today)];
  const counters = Object.fromEntries((await all(`SELECT name, count, bytes FROM counters WHERE name IN (${names.map(() => "?").join(", ")})`, ...names))
    .map((row) => [row.name, row]));
  const [devices] = await all("SELECT count(*) AS seen, sum(conversions > 0) AS converted, sum(dev) AS dev FROM devices");
  const [folded] = await all("SELECT max(day) AS day FROM folded");
  return {
    today, counters, devices, lastFolded: folded?.day ?? null,
    // A run's report or a readout's (reports.js holds either): a readout's has no words of its own.
    held: await all(`SELECT held_reports.id, held_reports.day, why, 'run' AS of, device, model, tool, result, step, error,
        text, tag, question, stock_check
      FROM held_reports JOIN reports ON reports.id = held_reports.id
      UNION ALL SELECT held_reports.id, held_reports.day, why, 'readout', device, model, tool, result, step, error,
        NULL, tag, question, stock_check
      FROM held_reports JOIN readout_reports ON readout_reports.id = held_reports.id
      ORDER BY 2 DESC LIMIT 100`),
    heldCount: (await all("SELECT count(*) AS n FROM held_reports"))[0].n,
    heldGroups: await all(`SELECT held_reports.day, why, ${NAMED} AS named, max(device) AS device, count(*) AS n
      FROM held_reports JOIN reports ON reports.id = held_reports.id GROUP BY held_reports.day, why, named
      ORDER BY held_reports.day DESC, n DESC`),
    recent: await all(`SELECT day, tag, tool, result, step, error, text, device, model, firmware, source, os_family, seconds, quarantined,
        crash_site, firmware_name, firmware_name_expected AS name_expected, question, stock_check
      FROM reports ORDER BY day DESC LIMIT 40`),
    recentReadouts: await all(`SELECT day, tag, tool, result, step, error, question, device, model, stock_model,
        stock_version, whole, os_family, seconds, quarantined, stock_check
      FROM readout_reports
      ORDER BY day DESC LIMIT 40`),
    week: await all(`SELECT day, ${OUTCOMES.map(([, results], at) =>
      `sum(result IN (${results.map((result) => `'${result}'`).join(", ")})) AS o${at}`).join(", ")}
      FROM reports WHERE day >= ? AND quarantined = 0 GROUP BY day ORDER BY day`, since(13)),
    store: await all("SELECT day, readouts, bytes FROM store_days WHERE day >= ? ORDER BY day", since(180)),
    mail: await all("SELECT day, outcome, code, n FROM mail_days WHERE day >= ? ORDER BY day", since(30)),
    readouts: await all("SELECT review, count(*) AS n, sum(bytes) AS bytes FROM readouts GROUP BY review ORDER BY review"),
    signups: (await all("SELECT count(*) AS n FROM confirmations"))[0]?.n ?? 0,
    settings: Object.fromEntries((await all("SELECT name, value FROM settings")).map((row) => [row.name, row.value])),
    nearly: await all(NEARLY),
    disputed: await all(DISPUTED),
    offers: await all("SELECT key, offered, day FROM agreed_offers ORDER BY day DESC, key LIMIT 200"),
    agreed: (await all("SELECT count(*) AS n FROM agreed_offers"))[0]?.n ?? 0,
    // Offers sent as links, and the files the API fetched from them (api/offered.js).
    links: await linkFigures(env.DB),
    unfetched: await unfetched(env.DB),
    review: await linksToReview(env.DB),
    // Links to review not kept, the queue being full, over the days the counts are kept.
    notKept: (await all("SELECT coalesce(sum(count), 0) AS n FROM counters WHERE name GLOB ?", linksNotKeptOn("*")))[0].n,
    every: await everyLink(env.DB),
    offerRuns: await all(OFFER_RUNS, since(OFFER_DAYS - 1)),
    identity: await all(IDENTITY_CHECKS),
    networks: await all(`SELECT name, json_array_length(asns) AS asns, previous, taken, problem FROM network_lists
      ORDER BY name`),
    popularity: await modelTrends(all, now),
    needed: await all(NEEDED, since(NEEDED_DAYS)),
    emulator: await gatherEmulator(all, now),
    // Every night GitHub was read, beside your users' devices converted by then.
    downloads: await downloadDays(all, "", "dev = 0"),
    systems: await all(SYSTEMS),
    // The last night GitHub refused the downloads, and why (downloads.js).
    downloadMiss: (await all("SELECT day, problem FROM download_misses ORDER BY day DESC LIMIT 1"))[0] ?? null,
  };
}

// What a run should never need - the device's own network, why, and the device
// switched off and on - by the stage that needed it, over the last NEEDED_DAYS:
// a stage that keeps needing either is where to look. Of the stages runs went
// through, the maintainer's own testing and quarantined reports left out.
export const NEEDED_DAYS = 90;
// The reasons a stage's setup network gives (report-schema.json), in its order.
export const REASONS = schema.properties.stages.additionalProperties.properties.setup_network.enum.filter(Boolean);
const NEEDED = `SELECT s.stage, count(*) AS runs, sum(s.setup_network IS NOT NULL) AS setup,
    ${REASONS.map((why, at) => `sum(s.setup_network = '${why.replace(/'/g, "")}') AS why${at}`).join(", ")},
    sum(s.power_cycles > 0) AS cycled, sum(s.power_cycles) AS cycles
  FROM report_stages AS s JOIN reports AS r ON r.id = s.id
  WHERE r.day >= ? AND r.tag = 'user' AND r.quarantined = 0
  GROUP BY s.stage HAVING sum(s.setup_network IS NOT NULL) + sum(s.power_cycles > 0) > 0
  ORDER BY sum(s.setup_network IS NOT NULL) + sum(s.power_cycles > 0) DESC, s.stage`;

// The offers runs send beside them (tools/autoflash/offers.py), as their
// reports count them (report_offers), day by day over the last OFFER_DAYS:
// the runs that asked eWeLink anything, the offers they sent and the website
// took, and how many saw eWeLink end the sign-in as they asked, and how many
// had the run's own sign-in ended after (of those that could tell): whether
// offers come in from people's runs, and whether asking for them loads
// eWeLink. The maintainer's own testing, and quarantined and held reports,
// left out. Private, so every day is shown, however few its runs.
export const OFFER_DAYS = 30;
const OFFER_RUNS = `SELECT r.day, count(*) AS runs, sum(o.sent) AS sent, sum(o.taken) AS taken,
    sum(o.session_ended) AS ended, sum(o.run_session_ended) AS runEnded, sum(o.run_session_ended IS NOT NULL) AS runKnown
  FROM report_offers AS o JOIN reports AS r ON r.id = o.id
  WHERE r.day >= ? AND r.tag = 'user' AND r.quarantined = 0 AND r.id NOT IN (SELECT id FROM held_reports)
    AND o.requests > 0
  GROUP BY r.day ORDER BY r.day DESC`;
// The identity check runs and readouts send (api/migrations/0006): by model
// and field, how many reports said each outcome - whether a device's identity
// could be rebuilt from its eWeLink account record. Your own testing, the
// emulator's included, and held reports left out.
export const IDENTITY_OUTCOMES = schema.properties.identity_check.properties.fields.additionalProperties.enum;
const IDENTITY_CHECKS = `SELECT coalesce(r.model, r.device, '') AS model, r.platform, c.field,
    ${IDENTITY_OUTCOMES.map((outcome, at) => `sum(c.outcome = '${outcome.replace(/'/g, "")}') AS o${at}`).join(", ")}
  FROM identity_checks AS c JOIN (
    SELECT id, model, device, identity_check_platform AS platform FROM reports WHERE tag = 'user' AND quarantined = 0
    UNION ALL SELECT id, model, device, identity_check_platform FROM readout_reports WHERE tag = 'user' AND quarantined = 0
  ) AS r ON r.id = c.id
  GROUP BY 1, 2, 3 ORDER BY 1, 2, 3`;
// The rows' `key` added up, and a share as a percentage to one decimal, "-" of none.
const total = (rows, key) => rows.reduce((sum, row) => sum + (row[key] ?? 0), 0);
const share = (part, whole) => (whole ? `${Math.round((1000 * part) / whole) / 10}%` : "-");

// How many months the popularity charts go back, and how many models they show.
export const POPULAR_MONTHS = 24;
export const POPULAR_MODELS = 24;

// Each model's devices, month by month, the last POPULAR_MONTHS (popularity.js):
// the POPULAR_MODELS models with the most, most first, each as trends() draws it.
export async function modelTrends(all, now) {
  const months = monthsTo(now, POPULAR_MONTHS);
  const models = new Map();
  for (const row of await popularity(all, `${months[0]}-01`)) {
    const model = models.get(row.model) ?? { model: row.model, series: {}, total: 0 };
    for (const [key, name] of Object.entries(SERIES)) {
      if (row[key]) {
        (model.series[name] ??= {})[row.month] = row[key];
        model.total += row[key];
      }
    }
    models.set(row.model, model);
  }
  return { months, models: [...models.values()].sort((a, b) => b.total - a.total || a.model.localeCompare(b.model))
    .slice(0, POPULAR_MODELS) };
}

// How fast the store fills: bytes a day over its last 30 days, and the day it
// would be full at that pace, or null.
export function pace(store, now) {
  const recent = store.slice(-30);
  if (recent.length < 2) {
    return null;
  }
  const days = (Date.parse(recent.at(-1).day) - Date.parse(recent[0].day)) / DAY || 1;
  const perDay = (recent.at(-1).bytes - recent[0].bytes) / days;
  const full = perDay > 0 ? dayOf(new Date(now.getTime() + ((TOTAL_BYTES - recent.at(-1).bytes) / perDay) * DAY)) : null;
  return { perDay, full };
}

// A link to review's first bytes (api/offered.js peekNamed), as text: how
// the server answered, then what they start like and as hex.
function peekShown(row) {
  if (!row.outcome) {
    return esc("Not asked yet.");
  }
  const said = [row.outcome, row.status, row.server && `from ${row.server}`, row.size != null && `${bytes(row.size)} in all`,
    row.location && `to ${row.location}`, row.kind, row.names].filter((part) => part !== null && part !== undefined
    && part !== false && part !== "");
  return esc(said.join(" · ")) + (row.hex ? `<br><code class="peek">${esc(row.hex)}</code>` : "");
}

// How often the view loads itself again when it is asked to keep up (?live=1),
// in seconds: emulator runs come in as they end.
export const LIVE_SECONDS = 60;

export async function page(env, who, now, fetcher = fetch, live = false) {
  const data = await gather(env, now);
  const used = await usage(env, now, fetcher);
  const count = (name) => data.counters[name]?.count ?? 0;
  const ever = data.counters["readouts:all"] ?? { count: 0, bytes: 0 };
  const filling = pace(data.store, now);
  const read = data.downloads.at(-1);
  const mailDays = Object.values(data.mail.reduce((days, row) => {
    days[row.day] ??= { day: row.day, parts: {} };
    days[row.day].parts[row.outcome] = (days[row.day].parts[row.outcome] ?? 0) + row.n;
    return days;
  }, {}));
  const codes = Object.entries(data.mail.filter((row) => row.outcome === "failed")
    .reduce((sum, row) => ({ ...sum, [row.code]: (sum[row.code] ?? 0) + row.n }), {})).map(([code, n]) => ({ code, n }));
  const action = (id, name, label, danger = false) => `<form class="inline" method="post" action="/held">`
    + `<input type="hidden" name="id" value="${esc(id)}"><input type="hidden" name="action" value="${name}">`
    + `<button${danger ? ' class="danger"' : ""}>${label}</button></form>`;
  const releaseAll = (row) => `<form class="inline" method="post" action="/held">`
    + `<input type="hidden" name="action" value="release-all"><input type="hidden" name="day" value="${esc(row.day)}">`
    + `<input type="hidden" name="why" value="${esc(row.why)}"><input type="hidden" name="named" value="${esc(row.named)}">`
    + `<button>Release all ${number(row.n)}</button></form>`;
  // Each copy field's id, the page's own.
  let copies = 0;
  const copy = (value) => copyField(`copy-${(copies += 1)}`, value);
  const linkForm = (row, action, label, danger = false) => `<form class="inline" method="post" action="/links">`
    + `<input type="hidden" name="readout" value="${esc(row.readout)}"><input type="hidden" name="sha" value="${esc(row.sha)}">`
    + `<input type="hidden" name="action" value="${action}"><button${danger ? ' class="danger"' : ""}>${label}</button></form>`;
  const switches = Object.entries(SWITCHES).map(([name, label]) => {
    const on = data.settings[name] !== "off";
    return `<form class="inline" method="post" action="/settings"><input type="hidden" name="name" value="${esc(name)}">`
      + `<input type="hidden" name="value" value="${on ? "off" : "on"}">${esc(label)}: <strong>${on ? "on" : "off"}</strong> `
      + `<button>${on ? "Turn off" : "Turn on"}</button></form>`;
  }).join("<br>");
  const sections = [
    `<section class="card"><div class="tiles">`
      + tile("Reports today", number(count(`reports:${data.today}`)), `of ${number(REPORTS_DAILY)} a day`)
      + tile("Readouts today", number(count(`readouts:${data.today}`)), `of ${number(DAILY_COUNT)}`)
      + tile("Emails today", number(count(`mail-sent:${data.today}`)), `${number(count(`notify:${data.today}`))} sign-ups of ${number(NOTIFY_DAILY)}`)
      + tile("Devices", number(data.devices?.seen), `${number(data.devices?.converted)} converted · ${number(data.devices?.dev)} yours`)
      + tile("Downloads", number(read?.downloads), [read ? `to ${read.day}` : "GitHub not read yet",
        // Refused since the last read: why, the latest night.
        data.downloadMiss && data.downloadMiss.day > (read?.day ?? "") && `refused for ${data.downloadMiss.day}: ${data.downloadMiss.problem}`]
        .filter(Boolean).join(" · "))
      + tile("Devices seen, by system", number(data.systems[0]?.seen), data.systems.slice(1)
        .map((row) => `${SYSTEM_NAMES[row.os] ?? row.os} ${number(row.seen)}`).join(" · ") || "no runs yet")
      + tile("Held for review", number(data.heldCount))
      + tile("Last night's job counted to", data.lastFolded ?? "not yet run")
      + `</div></section>`,
    `<section class="card"><h2>Reports, the last fortnight</h2><p class="sub">Every run's report that came in, by day and
      how the run ended, so few conversions can be told from few runs; held ones are in no figure.</p>`
      + columns(data.week.map((row) => ({ day: row.day,
        parts: Object.fromEntries(OUTCOMES.map(([name], at) => [name, row[`o${at}`]])) })),
      OUTCOMES.map(([name]) => name), "Reports a day, by how the run ended", FULL, OUTCOMES.map(([, , kind]) => kind))
      + `</section>`,
    `<section class="card" id="downloads"><h2>Downloads and devices converted</h2><p class="sub">The tool downloaded, as
      GitHub counts it each night (${DOWNLOADS.join(" and ")} of every release), beside the devices
      converted, your own left out: all time to each day.</p>`
      + lines(data.downloads.map((row) => ({ day: row.day, values: { downloads: row.downloads, "devices converted": row.converted } })),
        ["downloads", "devices converted"], "Downloads and devices converted by day")
      + `<h3>By system</h3><p class="sub">Who ran the tool, by the system each report says it ran on: a device run from
      two is in each, once in all.</p>`
      + table([["System", (row) => SYSTEM_NAMES[row.os] ?? row.os], ["Runs", "runs", "number"],
        ["Devices seen", "seen", "number"], ["Devices converted", "converted", "number"]],
      data.systems[0]?.runs ? data.systems : [], "No runs yet.")
      + `</section>`,
    `<section class="card" id="popularity"><h2>Devices, model by model</h2><p class="sub">Each month of the last
      ${POPULAR_MONTHS}: devices whose readout was first asked about, devices read out, and devices converted (again,
      too) - your own testing left out. The ${POPULAR_MODELS} models with the most, on one scale.</p>`
      + trends(data.popularity.models, data.popularity.months, Object.values(SERIES))
      + `</section>`,
    `<section class="card" id="needed"><h2>Setup network and power cycles, by stage</h2><p class="sub">The last
      ${NEEDED_DAYS} days: the stages that needed this computer on the device's own network (and why), or the device
      switched off and on, out of the runs that went through them. A run should need neither.</p>`
      + table([["Stage", "stage"], ["Runs through it", "runs", "number"], ["Setup network", "setup", "number"],
        ...REASONS.map((why, at) => [`- ${why}`, `why${at}`, "number"]),
        ["Power cycled", "cycled", "number"], ["Power cycles", "cycles", "number"]], data.needed, "None needed either.")
      + `</section>`,
    `<section class="card" id="held"><h2>Held for review</h2><p class="sub">Reports no run or readout of the tool's would write, or a day far
      above a device's usual. Release one to count it (its day is counted again tonight), or delete it; or release
      all of one device's held on one day for one reason at once. The newest 100 are listed.</p>`
      + (data.heldGroups.length > 1 || data.heldGroups[0]?.n > 1 ? table([["Day", "day"], ["Why", "why"],
        ["Device", (row) => deviceName(row) || row.named], ["Held", "n", "number"], ["", releaseAll, "html"]],
      data.heldGroups, "") : "")
      + table([["Day", "day"], ["Why", "why"], ["Of a", "of"], ["Device", deviceName], ["Model", "model"], ["Result", "result"], ["Stopped", stoppedAt], STOCK_CHECK,
        ["Words", "text", "text"], ["", (row) => action(row.id, "release", "Release") + " " + action(row.id, "delete", "Delete", true), "html"]],
      data.held, "Nothing held.")
      + `</section>`,
    `<section class="card" id="nearly"><h2>Nearly agreed</h2><p class="sub">${PER_KEY} or more readouts alike, waiting
      only because they came from fewer than ${MIN_NETWORKS} networks. Approve them to take them as right now, or leave them
      for a readout from another network.</p>`
      + table([["Key", "key"], ["Stage", "stage"], ["Offered", (row) => (row.stage === "first" ? row.offered : "")],
        ["Alike", "n", "number"], ["Networks", "networks", "number"],
        ["Last came", (row) => String(row.last).slice(0, 10)], ["", (row) => `<form class="inline" method="post" action="/approve">`
          + `<input type="hidden" name="key" value="${esc(row.key)}"><input type="hidden" name="stage" value="${esc(row.stage)}">`
          + `<input type="hidden" name="fingerprint" value="${esc(row.fingerprint)}"><button>Approve</button></form>`, "html"]],
      data.nearly, "None.")
      + `</section>`,
    `<section class="card" id="disputed"><h2>Disputed</h2><p class="sub">Keys whose whole readouts were taken as
      right, and a later one checking them is not alike. One of them may be wrong: look at them, and approve or reject
      each with readouts.py (review marks the later ones DISAGREES).</p>`
      + table([["Key", "key"], ["Stage", "stage"], ["Taken as right", "agreed", "number"],
        ["Disagreeing", "disagree", "number"], ["Last came", (row) => String(row.last).slice(0, 10)]],
      data.disputed, "None.")
      + `</section>`,
    `<section class="card" id="offers"><h2>Offers agreed</h2><p class="sub">What eWeLink offers each device, as
      ${PER_KEY} or more readouts - first stages or whole ones - from ${MIN_NETWORKS} networks agreed on it, newest
      first.</p>`
      + table([["Key", "key"], ["Offered", "offered"], ["Since", "day"]], data.offers, "None yet.")
      + `</section>`,
    `<section class="card" id="review"><h2>Links to review</h2><p class="sub">Links of a shape not seen before, as
      sent, never fetched until you approve one; approving fetches its file by it (a server named by address never:
      its path from each of eWeLink's servers by name), kept only if it is the SHA-256 given. Its first ${PEEK_BYTES}
      bytes were asked for once, when first sent. Approved ones whose file is not held are listed until it is.
      ${number(PENDING_LINKS_MOST)} wait at most, ${PENDING_LINKS_RENTED} of them sent only from networks that count as
      none; one not settled is deleted ${PENDING_LINK_DAYS} days after it was
      sent, and comes back if sent again. Those sent from the most networks first, then the longest waiting.</p>`
      + (data.notKept ? `<p class="sub"><strong>${number(data.notKept)} not kept in the last ${COUNTER_DAYS} days: the
      queue was full, or their sender had sent ${PENDING_LINKS_PER_SENDER} new ones that day.</strong></p>` : "")
      + table([["Device", (row) => String(row.key ?? "").split("|").slice(0, 3).join(" · ")],
        ["SHA-256", "sha", "text"], ["Networks", "networks", "number"],
        ["First seen", (row) => String(row.first ?? "").slice(0, 10)],
        ["Link", (row) => copy(sentAs(row))
          + (row.review === "pending" && fetchedFrom(row)[0] !== sentAs(row)
            ? `<br>Approving tries:${fetchedFrom(row).map(copy).join("")}` : ""), "html"],
        ["First bytes", peekShown, "html"],
        ["", (row) => (row.review === "pending"
          ? `${linkForm(row, "approve", "Approve")} ${linkForm(row, "reject", "Reject", true)}`
          : `${esc(row.failed ? "Approved: no server gave the file." : "Approved, not fetched yet.")} `
            + linkForm(row, "approve", "Try again")), "html"]],
      data.review, "None.")
      + `</section>`,
    `<section class="card" id="links"><h2>Offers sent as links</h2><p class="sub">First stages, and whole readouts,
      that sent the files eWeLink offers their device as links, and the files the API fetched from those links: each
      on first sight, and again once its offer is agreed. A file missing for an agreed offer is tried again each
      night.</p><div class="tiles">`
      + tile("Sent as links", number(data.links.sent))
      + tile("Offers agreed", number(data.agreed))
      + tile("Files fetched from links", number(data.links.fetched), bytes(data.links.bytes))
      + tile("Files missing", number(data.unfetched.reduce((sum, row) => sum + row.files, 0)),
        `for ${number(data.unfetched.length)} agreed offers`)
      + tile("Fetches today", number(count(fetchesOn(data.today))), `of ${number(OFFER_FETCHES_DAILY)} a day`)
      + tile("First sights today", number(count(firstSightsOn(data.today))), `of ${number(FIRST_SIGHT_DAILY)} a day`)
      + `</div>`
      + table([["Key", "key"], ["Sent as links", "sent", "number"], ["Offers agreed", "agreed", "number"],
        ["Files fetched", "fetched", "number"], ["Bytes", (row) => bytes(row.bytes)]], data.links.keys, "None yet.")
      + `<h3>Files missing for an agreed offer</h3>`
      + table([["Key", "key"], ["Offered", "offered"], ["Agreed", "day"], ["Files missing", "files", "number"]],
        data.unfetched, "None.")
      + `<h3>Every link</h3><p class="sub">Each file's link, as the API fetches it; a link to review not approved,
      as sent.</p>`
      + table([["Device", "key"], ["SHA-256", "sha", "text"], ["Review", "review"],
        ["File", (row) => (row.held ? "held" : "not held")], ["Sent", "sent", "number"],
        ["Link", (row) => (["auto", "approved"].includes(row.review) ? fetchedFrom(row) : [sentAs(row)]).map(copy)
          .join(""), "html"]], data.every, "None yet.")
      + `</section>`,
    `<section class="card" id="offer-runs"><h2>Offers sent beside runs</h2><p class="sub">Runs that asked eWeLink which
      firmware it offers the devices in their account, the last ${OFFER_DAYS} days, by their reports: the offers
      they sent and the website took, how many saw eWeLink end the sign-in as they asked, and how many had the run's
      own sign-in ended after they had (of those that could tell). Your own testing, and held reports, left out.</p>`
      + `<div class="tiles">`
      + tile("Runs that asked", number(total(data.offerRuns, "runs")))
      + tile("Offers sent", number(total(data.offerRuns, "sent")), `${number(total(data.offerRuns, "taken"))} taken`)
      + tile("Sign-in ended as they asked", share(total(data.offerRuns, "ended"), total(data.offerRuns, "runs")),
        `${number(total(data.offerRuns, "ended"))} of ${number(total(data.offerRuns, "runs"))} runs`)
      + tile("Run's sign-in ended after", share(total(data.offerRuns, "runEnded"), total(data.offerRuns, "runKnown")),
        `${number(total(data.offerRuns, "runEnded"))} of ${number(total(data.offerRuns, "runKnown"))} runs`)
      + `</div>`
      + table([["Day", "day"], ["Runs that asked", "runs", "number"], ["Offers sent", "sent", "number"],
        ["Taken", "taken", "number"], ["Sign-in ended", (row) => share(row.ended, row.runs)],
        ["Run's sign-in ended after", (row) => share(row.runEnded, row.runKnown)]], data.offerRuns, "None yet.")
      + `</section>`,
    `<section class="card" id="identity"><h2>Identity rebuilt from the account</h2><p class="sub">Whether a
      device's eWeLink identity, as its runs and readouts read it, is what its account record rebuilds, field by
      field: how many reports said each, by model. Checked on people's computers; only the outcomes are sent.
      Your own testing left out.</p>`
      + table([["Model", "model"], ["Platform", "platform"], ["Field", "field"],
        ...IDENTITY_OUTCOMES.map((outcome, at) => [outcome[0].toUpperCase() + outcome.slice(1), `o${at}`, "number"])],
      data.identity, "None yet.")
      + `</section>`,
    `<section class="card" id="networks"><h2>Networks that count as none</h2><p class="sub">Hosts' and VPNs' networks,
      which count toward no agreement of readouts or of models' names: ${number(CLOUD_ASNS.size)} kept in the code,
      and these lists, fetched each night.</p>`
      + table([["List", "name"], ["Networks", "asns", "number"], ["Before", "previous", "number"], ["Taken", "taken"],
        ["Last try", (row) => row.problem ?? "taken"]], data.networks, "Not fetched yet.")
      + `</section>`,
    `<div class="grid2"><section class="card"><h2>The store of readouts</h2><p class="sub">What it holds each night, against its limit.</p>`
      + meter("Held", ever.bytes, TOTAL_BYTES, `${bytes(ever.bytes)} of ${bytes(TOTAL_BYTES)} · ${number(ever.count)} readouts`)
      + lineChart(data.store.map((row) => ({ day: row.day, value: row.bytes })), { limit: TOTAL_BYTES, say: bytes, label: "Readout store by day" })
      + `<p class="sub">${filling ? `Filling at ${bytes(Math.max(0, filling.perDay))} a day over the last month`
        + (filling.full ? `: full by about ${esc(filling.full)} at that pace.` : ".") : "Not enough days yet to say how fast it fills."}</p>`
      + table([["Review", "review"], ["Readouts", "n", "number"], ["Bytes", (row) => bytes(row.bytes)]], data.readouts, "No readouts yet.")
      + `<p class="sub">Daily limits: ${number(DAILY_COUNT)} readouts and ${bytes(DAILY_BYTES)}.</p></section>`,
    `<section class="card"><h2>Email</h2><p class="sub">Sent and failed by day, the last month; ${number(data.signups)} sign-ups waiting to be confirmed.</p>`
      + columns(mailDays, ["sent", "failed"], "Emails a day")
      + table([["Failed with", "code"], ["Emails", "n", "number"]], codes, "No failures.")
      + `</section></div>`,
    ...emulatorSections(data.emulator),
    `<section class="card" id="switches"><h2>Switches</h2><p class="sub">Emails sent to the maintainer's address
      (ALERTS_RECIPIENT_EMAIL), beside the nightly alert.</p>${switches}</section>`,
    `<section class="card"><h2>This month's use</h2><p class="sub">Against what Workers Paid includes (R2: its own free allowance); past it, it is billed.</p>`
      + (typeof used === "string" ? `<p class="none">${esc(used)}</p>`
        : Object.entries(INCLUDED).map(([what, of]) => meter(what, used[what], of,
          `${what.includes("bytes") ? bytes(used[what]) : number(used[what])} of ${what.includes("bytes") ? bytes(of) : number(of)}`, 0.5)).join(""))
      + `</section>`,
    `<section class="card"><h2>The latest reports</h2><p class="sub">The newest 40, held ones marked. At most ${number(DEVICE_DAILY)} a day of one device are taken.</p>`
      + table([["Day", "day"], ["Tag", "tag"], ["Tool", "tool"], ["Result", (row) => row.result + (row.quarantined ? " (held)" : "")],
        ["Stopped", stoppedAt], STOCK_CHECK, ["Device", deviceName], ["Model", "model"], ["To", "firmware"], ["From", "source"],
        ["System", "os_family"], ["Seconds", "seconds", "number"], ["Words", "text", "text"], ["Went wrong at", "crash_site", "text"],
        // The name the device's updater checks; one not the tool's may turn its update down.
        ["Firmware name", (row) => (row.firmware_name ?? "") + (row.name_expected === 0 ? " (not the tool's)" : "")]],
        data.recent, "No reports yet.")
      + `</section>`,
    `<section class="card" id="readout-reports"><h2>The latest readout reports</h2><p class="sub">The newest 40, one
      a device read out, held ones marked: where one stopped, the stage it was in and the question it was stopped at.</p>`
      + table([["Day", "day"], ["Tag", "tag"], ["Tool", "tool"], ["Result", (row) => row.result + (row.quarantined ? " (held)" : "")],
        ["Stopped", stoppedAt], STOCK_CHECK, ["Device", deviceName], ["Model", "model"], ["Sonoff model", "stock_model"],
        ["Sonoff version", "stock_version"], ["Whole", (row) => (row.whole === null ? "" : row.whole ? "yes" : "no")],
        ["System", "os_family"], ["Seconds", "seconds", "number"]],
      data.recentReadouts, "No readout reports yet.")
      + `</section>`,
  ];
  return shellPage("Admin", `<div class="admin"><div class="top"><h1>Admin</h1><span class="who">`
    + `${esc(who.email ?? "")} · ${esc(data.today)} · <a href="${SITE}/stats">public figures</a> · `
    + (live ? `<a href="/">stop updating</a>` : `<a href="/?live=1">update every ${LIVE_SECONDS} s</a>`)
    + `</span></div>${sections.join("")}</div>`,
  { heading: false, script: "", more: `<style>${STYLE}</style><script src="/assets/copy.js" defer></script>`
    + (live ? `<meta http-equiv="refresh" content="${LIVE_SECONDS}">` : "") });
}
