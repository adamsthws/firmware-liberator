// api/alerts.js: the maintainer's email when a limit was reached, counts only.
import assert from "node:assert/strict";
import { test } from "node:test";
import { alert, alertLines, STORE_WARN } from "../api/alerts.js";
import { LIST_STALE_DAYS } from "../api/networks.js";
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
