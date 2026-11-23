// api/alerts.js: the maintainer's email when a limit was reached, counts only.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { alert, alertLines, STORE_WARN } from "../api/alerts.js";
import { DOWNLOADS_STALE_NIGHTS, TOKEN_FIX } from "../api/downloads.js";
import { LIST_JUMP, LIST_STALE_DAYS } from "../api/networks.js";
import { OFFER_FETCH_ALERT_DAYS } from "../api/offered.js";
import { writes } from "../api/reports.js";
import worker from "../api/index.js";
import { d1 } from "../api/local-d1.mjs";

// Nothing here reaches the internet: the nightly cron's fetch of the lists of
// networks (networks.js) fails, as it may, and is told so.
globalThis.fetch = async () => {
  throw new Error("no network in the tests");
};

const TODAY = "2026-11-10";
const LIMITS = { DAILY_COUNT: 100, DAILY_BYTES: 1000, TOTAL_BYTES: 10_000, NOTIFY_DAILY: 50, REPORTS_DAILY: 1000,
  from: "updates@firmwareliberator.com" };

function env({ failing = false } = {}) {
  const sent = [];
  return { DB: d1(), ALERTS_RECIPIENT_EMAIL: "owner@example.com", sent, EMAIL: { send: async (message) => {
    if (failing) throw new Error("Email Service is down");
    sent.push(message);
  } } };
}

const count = (bindings, name, day, n, bytes = 0) => bindings.DB.db
  .prepare("INSERT INTO counters (name, day, count, bytes) VALUES (?, ?, ?, ?)").run(name, day, n, bytes);
const stored = (bindings, day, bytes) => bindings.DB.db
  .prepare("INSERT INTO store_days (day, readouts, bytes) VALUES (?, 1, ?)").run(day, bytes);

test("nothing reached, nothing sent", async () => {
  const bindings = env();
  count(bindings, "reports:2026-11-09", "2026-11-09", 999);
  assert.deepEqual(await alert(bindings, TODAY, LIMITS), { sent: false, lines: [] });
  assert.equal(bindings.sent.length, 0);
});

test("each of yesterday's limits for everyone, reached, is a line", async () => {
  const bindings = env();
  count(bindings, "readouts:2026-11-09", "2026-11-09", 3, 1000);
  count(bindings, "reports:2026-11-09", "2026-11-09", 1000);
  count(bindings, "notify:2026-11-09", "2026-11-09", 50);
  count(bindings, "reports:2026-11-08", "2026-11-08", 5000);   // the day before: told then
  const lines = await alertLines(bindings.DB, TODAY, LIMITS);
  assert.equal(lines.length, 3);
  assert.match(lines[0], /limit of readouts was reached on 2026-11-09: 3 of 100/);
  assert.match(lines[1], /limit of reports was reached on 2026-11-09: 1000 of 1000/);
  assert.match(lines[2], /addresses given was reached on 2026-11-09: 50 of 50/);
});

test("the store is told of the night it passes its mark, not each night after", async () => {
  const bindings = env();
  const mark = STORE_WARN * LIMITS.TOTAL_BYTES;
  stored(bindings, "2026-11-09", mark - 1);
  stored(bindings, TODAY, mark);
  assert.match((await alertLines(bindings.DB, TODAY, LIMITS))[0], /past 75%/);
  stored(bindings, "2026-11-11", mark + 10);
  bindings.DB.db.prepare("INSERT INTO download_days (day, downloads) VALUES ('2026-11-10', 5)").run();   // read too
  assert.deepEqual(await alertLines(bindings.DB, "2026-11-11", LIMITS), []);
});

test("it is sent to the maintainer, from the API's address, counts only", async () => {
  const bindings = env();
  count(bindings, "reports:2026-11-09", "2026-11-09", 1000);
  assert.equal((await alert(bindings, TODAY, LIMITS)).sent, true);
  const [message] = bindings.sent;
  assert.deepEqual([message.to, message.from], ["owner@example.com", LIMITS.from]);
  assert.match(message.subject, /one thing to look at/);
  assert.ok(!/@|\b\d{1,3}(\.\d{1,3}){3}\b/.test(message.text), "no address of anyone in it");
});

test("with no binding or no address, nothing is sent, and the job is told why", async () => {
  for (const bindings of [{ ...env(), ALERTS_RECIPIENT_EMAIL: undefined }, { ...env(), EMAIL: undefined }]) {
    count(bindings, "reports:2026-11-09", "2026-11-09", 1000);
    const done = await alert(bindings, TODAY, LIMITS);
    assert.deepEqual([done.sent, done.why], [false, "no EMAIL binding or ALERTS_RECIPIENT_EMAIL secret"]);
  }
});

test("an email that cannot be sent leaves the night's work done", async () => {
  const bindings = env({ failing: true });
  count(bindings, "reports:2026-11-09", "2026-11-09", 1000);
  const done = await alert(bindings, TODAY, LIMITS);
  assert.equal(done.sent, false);
  assert.match(done.why, /Email Service is down/);
});

test("yesterday's emails that failed are a line, but for those to a suppressed address", async () => {
  const bindings = env();
  count(bindings, "mail-failed:E_RECIPIENT_SUPPRESSED:2026-11-09", "2026-11-09", 4);
  assert.deepEqual(await alertLines(bindings.DB, TODAY, LIMITS), [], "expected now and then");
  count(bindings, "mail-failed:E_RATE_LIMIT_EXCEEDED:2026-11-09", "2026-11-09", 2);
  const lines = await alertLines(bindings.DB, TODAY, LIMITS);
  assert.deepEqual(lines, ["Emails that could not be sent on 2026-11-09: 2 E_RATE_LIMIT_EXCEEDED "
    + "(Cloudflare's Email Sending activity log has each)."]);
});

test("the nightly job is given the API's own limits", async () => {
  const bindings = { ...env(), STATS: { head: async () => null, put: async () => {} } };
  count(bindings, "reports:2026-11-09", "2026-11-09", 1000);
  const waited = [];
  await worker.scheduled({ cron: "17 3 * * *", scheduledTime: Date.parse("2026-11-10T03:17:00Z") }, bindings,
    { waitUntil: (promise) => waited.push(promise) });
  const done = await waited[0];
  assert.equal(done.alerted.sent, true, "1000 is the real limit of reports a day");
  assert.equal(bindings.sent[0].from, "updates@firmwareliberator.com");
  assert.equal(done.reminded, 0, "and sends the reminders after it");
});

test("a list of networks not taken is told once a week, never nightly", async () => {
  const bindings = env();
  const list = bindings.DB.db.prepare("INSERT INTO network_lists (name, asns, taken, problem) VALUES (?, '[]', ?, ?)");
  list.run("vpn", "2026-11-03", "HTTP 404");
  list.run("datacenter", "2026-11-09", "HTTP 404");
  assert.equal(LIST_STALE_DAYS, 7);
  assert.deepEqual(await alertLines(bindings.DB, TODAY, LIMITS), ["The vpn list of networks that count toward no "
    + "agreement (website/api/networks.js) has not been taken since 2026-11-03: HTTP 404."]);
  assert.deepEqual(await alertLines(bindings.DB, "2026-11-11", LIMITS), [], "not the next night");
  assert.equal((await alertLines(bindings.DB, "2026-11-17", LIMITS)).length, 1, "but a week on");
});

test("a list of networks taken far longer than the one kept is told that night, with some it added", async () => {
  const bindings = env();
  const list = bindings.DB.db.prepare(`INSERT INTO network_lists (name, asns, taken, problem, previous, added)
    VALUES (?, ?, ?, NULL, ?, ?)`);
  assert.equal(LIST_JUMP, 0.5);
  list.run("datacenter", JSON.stringify([1, 2, 3, 4, 5, 6, 7]), TODAY, 4, "[5,6,7]");
  list.run("vpn", JSON.stringify([1, 2, 3, 4, 5, 6]), TODAY, 4, "[5,6]");   // half as many again: not past it
  assert.deepEqual(await alertLines(bindings.DB, TODAY, LIMITS), ["The datacenter list of networks that count toward "
    + "no agreement (website/api/networks.js) grew from 4 to 7 today. Among those added: AS5, AS6, AS7. A home "
    + "internet provider among them would cost its customers their vote."]);
  assert.deepEqual(await alertLines(bindings.DB, "2026-11-11", LIMITS), [], "that night only");
});

test("the downloads gone unread from GitHub are told after three nights, then each three, never the night after", async () => {
  const bindings = env();
  assert.equal(DOWNLOADS_STALE_NIGHTS, 3);
  const read = (day) => bindings.DB.db.prepare("INSERT INTO download_days (day, downloads) VALUES (?, 5)").run(day);
  const lines = async (today) => (await alertLines(bindings.DB, today, LIMITS)).filter((line) => line.includes("downloads"));
  read("2026-11-09");   // read last night
  assert.deepEqual(await lines(TODAY), []);
  assert.deepEqual(await lines("2026-11-11"), [], "one night missed");
  assert.deepEqual(await lines("2026-11-13"), ["GitHub has not given the tool's downloads for 3 nights in a row, the last "
    + "kept for 2026-11-09 (website/api/downloads.js). Why: no reason kept (3 nights; the Worker's logs may say)."],
  "no reason kept: no fix guessed");
  assert.deepEqual(await lines("2026-11-14"), [], "not the next night");
  assert.equal((await lines("2026-11-16")).length, 1, "but three on");
});

const miss = (bindings, day, problem, limited) => bindings.DB.db
  .prepare("INSERT INTO download_misses (day, problem, rate_limited) VALUES (?, ?, ?)").run(day, problem, limited ? 1 : 0);

test("each night's why is told, and the token recommended where GitHub's rate limit was one", async () => {
  const bindings = env();
  bindings.DB.db.prepare("INSERT INTO download_days (day, downloads) VALUES ('2026-11-09', 5)").run();
  const limit = "GitHub's rate limit reached (HTTP 403, 60 requests an hour)";
  miss(bindings, "2026-11-08", "HTTP 500", false);   // before the last read: no part of it
  miss(bindings, "2026-11-10", limit, true);
  miss(bindings, "2026-11-11", "timed out", false);
  miss(bindings, "2026-11-12", limit, true);
  assert.deepEqual(await alertLines(bindings.DB, "2026-11-13", LIMITS), ["GitHub has not given the tool's downloads for "
    + "3 nights in a row, the last kept for 2026-11-09 (website/api/downloads.js). Why: GitHub's rate limit reached "
    + "(HTTP 403, 60 requests an hour) (2 nights); timed out (1 night). GitHub's rate limit for a request with no "
    + `token is shared by every Worker on the same addresses. ${TOKEN_FIX}`]);
  assert.match(TOKEN_FIX, /never expires.*read-only access to public repositories.*cloudflare_settings\.py apply/);
});

test("no token is recommended where none of the nights was GitHub's rate limit", async () => {
  const bindings = env();
  bindings.DB.db.prepare("INSERT INTO download_days (day, downloads) VALUES ('2026-11-09', 5)").run();
  miss(bindings, "2026-11-10", "HTTP 404", false);
  miss(bindings, "2026-11-12", "HTTP 404", false);   // the night between kept no reason
  const [line] = await alertLines(bindings.DB, "2026-11-13", LIMITS);
  assert.match(line, /Why: HTTP 404 \(2 nights\); no reason kept \(1 night; the Worker's logs may say\)\. None was GitHub's rate limit, so a token would not help: look at what it said\.$/);
  assert.ok(!line.includes(TOKEN_FIX));
});

test("downloads never read are counted from the first night the job ran", async () => {
  const bindings = env();
  const lines = async (today) => (await alertLines(bindings.DB, today, LIMITS)).filter((line) => line.includes("downloads"));
  assert.deepEqual(await lines(TODAY), [], "the job never ran: nothing to count from");
  stored(bindings, "2026-11-07", 0);   // the first night, unread
  assert.deepEqual(await lines("2026-11-08"), [], "two nights");
  assert.match((await lines("2026-11-09"))[0], /for 3 nights in a row, none ever/);
});

// An offer of KEY taken as right on `day`, a link given for its file, and
// whether that file is held.
function agreedOffer(bindings, day, held = false, key = "NEWTHING|SN-NEW-01|1.0.0") {
  const db = bindings.DB.db;
  db.prepare(`INSERT INTO readouts (id, key, stage, sender, fingerprint, bytes, object, received, review, offer, offered)
    VALUES (?1, ?2, 'first', ?1, 'o', 1, 'x', ?3, 'quorum', 'o', '1.2.0')`).run(`r-${key}`, key, `${day}T12:00:00Z`);
  db.prepare("INSERT INTO agreed_offers (key, offer, offered, day) VALUES (?, 'o', '1.2.0', ?)").run(key, day);
  db.prepare("INSERT INTO offer_links (readout, sha, path, cloud) VALUES (?, ?, '/ota/rom/x/y', 'ewelink')")
    .run(`r-${key}`, "a".repeat(64));
  if (held) {
    db.prepare("INSERT INTO readout_firmware (readout, sha) VALUES (?, ?)").run(`r-${key}`, "a".repeat(64));
  }
}
const daysBefore = (days) => new Date(Date.parse(`${TODAY}T00:00:00Z`) - days * 86_400_000).toISOString().slice(0, 10);

test("an offer's files the cloud has not given are told OFFER_FETCH_ALERT_DAYS after it agreed, and as often after",
  async () => {
    assert.equal(OFFER_FETCH_ALERT_DAYS, 3);
    for (const [days, told] of [[2, false], [3, true], [4, false], [5, false], [6, true]]) {
      const bindings = env();
      agreedOffer(bindings, daysBefore(days));
      const lines = await alertLines(bindings.DB, TODAY, LIMITS);
      assert.equal(lines.length, Number(told), `${days} days`);
      if (told) {
        assert.match(lines[0], new RegExp(`^The files of 1 offer taken as right have not been fetched .*`
          + `NEWTHING\\|SN-NEW-01\\|1\\.0\\.0 \\(offered 1\\.2\\.0, ${days} days\\)`));
        assert.match(lines[0], /set OFFERED_SEND to "files" in website\/api\/wrangler\.jsonc/);
      }
    }
  });

test("an offer whose files are held is not told", async () => {
  const bindings = env();
  agreedOffer(bindings, daysBefore(OFFER_FETCH_ALERT_DAYS), true);
  assert.deepEqual(await alertLines(bindings.DB, TODAY, LIMITS), []);
});

test("several offers missing files are one line, counting them all", async () => {
  const bindings = env();
  agreedOffer(bindings, daysBefore(3), false, "ONE|-|1.0.0");
  agreedOffer(bindings, daysBefore(4), false, "TWO|-|1.0.0");
  const lines = await alertLines(bindings.DB, TODAY, LIMITS);
  assert.equal(lines.length, 1);
  assert.match(lines[0], /^The files of 2 offers taken as right/);
});

// A run's report that refused a table (test/fixtures/reports.json's), kept
// as the API keeps one (reports.js writes), on `day`.
const REFUSED = JSON.parse(readFileSync(new URL("./fixtures/reports.json", import.meta.url), "utf8")).valid
  .find((sample) => sample.table_refused);
let refusals = 0;
async function refused(bindings, day, { sha256 = REFUSED.table_refused.sha256, at = REFUSED.table_refused.at,
  held = null, ...changes } = {}) {
  refusals += 1;
  const made = { ...REFUSED, ...changes, id: refusals.toString(16).padStart(16, "0"), table_refused: { sha256, at } };
  await bindings.DB.batch(writes(bindings.DB, made, null, day, held));
}

test("a table refused on a kind the tool converts is told the night after it is first refused, never again",
  async () => {
    const bindings = env();
    await refused(bindings, "2026-11-09");
    await refused(bindings, "2026-11-09", { model: "BASICR4-B" });
    await refused(bindings, TODAY);
    const lines = await alertLines(bindings.DB, TODAY, LIMITS);
    assert.deepEqual(lines, ["A partition table was refused on the Sonoff BASICR4 (model BASICR4, BASICR4-B), a "
      + `kind the tool converts, first on 2026-11-09: 3 reports, its SHA-256 ${REFUSED.table_refused.sha256}, read `
      + "at 0xc000. Its published table may be wrong, or a new batch laid out otherwise (tools/espressif/layouts.py, "
      + "tools/beken/layouts.py)."]);
    assert.deepEqual(await alertLines(bindings.DB, "2026-11-11", LIMITS), [], "told once");
    // Another table on a kind is new, and told.
    await refused(bindings, "2026-11-10", { sha256: "ab".repeat(32), at: "update-file", family: "beken",
      device: "sonoff-s61", model: "S61" });
    assert.match((await alertLines(bindings.DB, "2026-11-11", LIMITS)).join("\n"),
      /^A partition table was refused on the Sonoff S61 \(model S61\).*: 1 report, .* read from Sonoff's update file\./);
  });

test("a table refused on a kind the tool does not convert, by the maintainer, or held for review is not told",
  async () => {
    const bindings = env();
    await refused(bindings, "2026-11-09", { device: "sonoff-minir4", model: "MINIR4" });
    await refused(bindings, "2026-11-09", { tag: "dev" });
    await refused(bindings, "2026-11-09", { held: "words no run of the tool's would write" });
    assert.deepEqual(await alertLines(bindings.DB, TODAY, LIMITS), []);
  });
