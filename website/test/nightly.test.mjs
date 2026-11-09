// api/nightly.js and api/snapshot.js: what is kept of the reports for good,
// and the public figures made from it.
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { LATEST_MIGRATION } from "../api/database.js";
import { d1 } from "../api/local-d1.mjs";
import * as night from "../api/nightly.js";
import { writes } from "../api/reports.js";
import { SMALLEST } from "../api/snapshot.js";

const FIXTURE = JSON.parse(readFileSync(new URL("./fixtures/reports.json", import.meta.url), "utf8")).valid[0];
const NIGHT = new Date("2026-11-10T03:17:00Z");

// A report as /reports writes it, on `day`.
async function kept(db, day, changes = {}, hash = changes.device_hash ?? null) {
  const report = { ...structuredClone(FIXTURE), ...changes, id: randomBytes(16).toString("hex") };
  await db.batch(writes(db, report, hash, day));
  return report.id;
}

function bucket() {
  const objects = new Map();
  return {
    objects,
    head: async (key) => (objects.has(key) ? { key } : null),
    put: async (key, body, options) => objects.set(key, { body, options }),
  };
}

const env = () => ({ DB: d1(), STATS: bucket() });
const rows = (bindings, sql, ...values) => bindings.DB.db.prepare(sql).all(...values).map((row) => ({ ...row }));
const figures = (bindings) => JSON.parse(bindings.STATS.objects.get(night.SNAPSHOT_KEY).body);

test("each day before today is counted once, and today's are left for tomorrow", async () => {
  const bindings = env();
  await kept(bindings.DB, "2026-11-09");
  await kept(bindings.DB, "2026-11-09", { result: "failed", stopped: { step: "D4", error: "upload-cut", text: "x" } });
  await kept(bindings.DB, "2026-11-10");
  await night.nightly(bindings, NIGHT);
  await night.nightly(bindings, NIGHT);
  assert.deepEqual(rows(bindings, "SELECT day, result, step, error, n FROM daily_totals ORDER BY result"), [
    { day: "2026-11-09", result: "converted", step: "", error: "", n: 1 },
    { day: "2026-11-09", result: "failed", step: "D4", error: "upload-cut", n: 1 },
  ]);
  assert.deepEqual(rows(bindings, "SELECT day FROM folded"), [{ day: "2026-11-09" }]);
  assert.deepEqual(rows(bindings, "SELECT month, n FROM monthly_totals ORDER BY n"),
    [{ month: "2026-11", n: 1 }, { month: "2026-11", n: 1 }]);
});

test("a day unfolded after a report of it is quarantined is counted again without it", async () => {
  const bindings = env();
  const bad = await kept(bindings.DB, "2026-09-03");
  await kept(bindings.DB, "2026-09-03");
  await night.nightly(bindings, NIGHT);
  assert.equal(rows(bindings, "SELECT n FROM monthly_totals WHERE month = '2026-09'")[0].n, 2);
  bindings.DB.db.prepare("UPDATE reports SET quarantined = 1 WHERE id = ?").run(bad);
  bindings.DB.db.prepare("DELETE FROM folded WHERE day = '2026-09-03'").run();
  const ran = await night.nightly(bindings, NIGHT);
  assert.ok(ran.months.includes("2026-09"), "a month long past made again too");
  assert.equal(rows(bindings, "SELECT n FROM daily_totals WHERE day = '2026-09-03'")[0].n, 1);
  assert.equal(rows(bindings, "SELECT n FROM monthly_totals WHERE month = '2026-09'")[0].n, 1);
});

test("run and step times are a month's median and 90th percentile", async () => {
  const bindings = env();
  for (let n = 1; n <= 10; n += 1) {
    await kept(bindings.DB, "2026-11-03", { seconds: n * 100, stages: { A1: { s: n, busy: 0, tries: 1 } } });
  }
  await kept(bindings.DB, "2026-11-03", { seconds: 99999, result: "failed",
    stopped: { step: "D4", error: "other", text: "" } });   // not a conversion: not a run's time
  await night.nightly(bindings, NIGHT);
  assert.deepEqual(rows(bindings, "SELECT n, p50, p90 FROM monthly_times"), [{ n: 10, p50: 500, p90: 900 }]);
  assert.deepEqual(rows(bindings, "SELECT tool, step, n, p50, p90 FROM monthly_steps WHERE step = 'A1'"),
    [{ tool: "2.4.0", step: "A1", n: 11, p50: 6, p90: 10 }], "1 to 10, and the failed run's 12: every run's steps count");
});

test("reports a year old go once counted; a limit's day counts go after a week, the lasting ones never", async () => {
  const bindings = env();
  await kept(bindings.DB, "2025-11-09");
  await kept(bindings.DB, "2025-11-11");
  const counter = bindings.DB.db.prepare("INSERT INTO counters (name, day, count, bytes) VALUES (?, ?, 1, 0)");
  counter.run("reports:2026-11-02", "2026-11-02");
  counter.run("reports:2026-11-04", "2026-11-04");
  counter.run("readouts:all", null);
  await night.nightly(bindings, NIGHT);
  assert.deepEqual(rows(bindings, "SELECT day FROM reports"), [{ day: "2025-11-11" }]);
  assert.equal(rows(bindings, "SELECT n FROM daily_totals WHERE day = '2025-11-09'")[0].n, 1, "its total stays");
  assert.deepEqual(rows(bindings, "SELECT name FROM counters ORDER BY name"),
    [{ name: "readouts:all" }, { name: "reports:2026-11-04" }]);
});

test("pruning never deletes a report whose day is not yet counted, however old", async () => {
  const bindings = env();
  await kept(bindings.DB, "2024-01-01");
  await bindings.DB.batch(night.pruning(bindings.DB, "2026-11-10"));
  assert.equal(rows(bindings, "SELECT * FROM reports").length, 1);
});

test("a night counts an old report before it deletes it", async () => {
  const bindings = env();
  await kept(bindings.DB, "2024-01-01");
  bindings.DB.db.prepare("INSERT INTO folded (day) VALUES ('2024-01-02')").run();
  await night.nightly(bindings, NIGHT);
  assert.equal(rows(bindings, "SELECT * FROM daily_totals WHERE day = '2024-01-01'").length, 1, "counted first");
  assert.equal(rows(bindings, "SELECT * FROM reports").length, 0, "then gone");
});

test("the figures leave out the maintainer's testing, and anything under five", async () => {
  const bindings = env();
  const hashes = Array.from({ length: 6 }, (_, n) => `device-${n}`);
  for (const hash of hashes) {
    await kept(bindings.DB, "2026-11-05", { device_hash: hash });
  }
  await kept(bindings.DB, "2026-11-05", { device_hash: "dev-device", tag: "dev" });
  for (let n = 0; n < 4; n += 1) {
    await kept(bindings.DB, "2026-11-06", { model: "RARE1", device_hash: `rare-${n}`, device: null });
  }
  await night.nightly(bindings, NIGHT);
  const stats = figures(bindings);
  assert.equal(stats.devices.seen, 10, "the dev device left out");
  assert.equal(stats.runs.runs, 10);
  assert.deepEqual(stats.models.map((row) => [row.model, row.devices]), [["S60TPG", 6]], "RARE1 has four");
  assert.equal(stats.models[0].firstRun, 100);
  assert.ok(!JSON.stringify(stats).includes("RARE1"));
  assert.ok(!JSON.stringify(stats).includes("device-"), "no device's id");
});

test("a run declined, or with nothing to do, is not a failure", async () => {
  const bindings = env();
  const failed = (error) => ({ result: "failed", stopped: { step: "A3", error, text: "" } });
  for (let n = 0; n < 5; n += 1) {
    await kept(bindings.DB, "2026-11-05");
    await kept(bindings.DB, "2026-11-05", failed("declined"));
    await kept(bindings.DB, "2026-11-05", failed("nothing-to-do"));
  }
  for (let n = 0; n < 5; n += 1) {
    await kept(bindings.DB, "2026-11-05", failed("device-lost"));
  }
  await night.nightly(bindings, NIGHT);
  const { runs, failures } = figures(bindings);
  assert.deepEqual([runs.runs, runs.converted, runs.failed, runs.declined, runs.success], [20, 5, 5, 10, 50]);
  assert.deepEqual(failures, [{ step: "A3", error: "device-lost", runs: 5 }]);
});

test("the devices wanted: /wanted's questions and the runs stopped for want of support", async () => {
  const bindings = env();
  const ask = bindings.DB.db.prepare("INSERT INTO wanted_days (day, model, asked) VALUES (?, ?, ?)");
  ask.run("2026-11-01", "NSPANEL", 3);
  ask.run("2026-11-02", "NSPANEL", 3);
  ask.run("2026-11-02", "FEW", 2);
  for (let n = 0; n < 5; n += 1) {
    await kept(bindings.DB, "2026-11-05", { model: "BASICR4", device: null, result: "failed",
      stopped: { step: "A2", error: "unsupported-device", text: "" } });
  }
  await night.nightly(bindings, NIGHT);
  assert.deepEqual(figures(bindings).wanted, [
    { model: "NSPANEL", asked: 6, unsupportedRuns: null },
    { model: "BASICR4", asked: null, unsupportedRuns: 5 },
  ]);
});

test("the figures say what they are, how fresh, and for which schema", async () => {
  const bindings = env();
  await night.nightly(bindings, NIGHT);
  const { options } = bindings.STATS.objects.get(night.SNAPSHOT_KEY);
  const stats = figures(bindings);
  assert.deepEqual([stats.format, stats.generated, stats.through, stats.schema, stats.licence, stats.smallest],
    [1, "2026-11-10T03:17Z", "2026-11-09", LATEST_MIGRATION, "CC BY 4.0", SMALLEST]);
  assert.equal(options.httpMetadata.contentType, "application/json");
  assert.equal(stats.devices.seen, null, "none yet: under five");
});

test("one copy a month is kept, the first written", async () => {
  const bindings = env();
  await night.nightly(bindings, NIGHT);
  const first = bindings.STATS.objects.get("v1/archive/2026-11.json").body;
  await kept(bindings.DB, "2026-11-10");
  await night.nightly(bindings, new Date("2026-11-11T03:17:00Z"));
  assert.equal(bindings.STATS.objects.get("v1/archive/2026-11.json").body, first);
  assert.notEqual(bindings.STATS.objects.get(night.SNAPSHOT_KEY).body, first);
  await night.nightly(bindings, new Date("2026-12-01T03:17:00Z"));
  assert.ok(bindings.STATS.objects.has("v1/archive/2026-12.json"));
});

test("nothing is done before the database has every migration", async () => {
  const bindings = { ...env(), DB: d1({ migrated: false }) };
  assert.deepEqual(await night.nightly(bindings, NIGHT),
    { ran: false, why: "the database does not have every migration yet" });
  assert.equal(bindings.STATS.objects.size, 0);
});

test("a night costs a fixed few queries, well within D1's 50", async () => {
  const bindings = env();
  for (let day = 1; day <= 9; day += 1) {
    await kept(bindings.DB, `2026-11-0${day}`);
  }
  let queries = 0;
  const db = bindings.DB;
  const counted = { ...db, prepare: (sql) => {
    const wrap = (inner) => ({ ...inner, inner, bind: (...values) => wrap(inner.bind(...values)),
      first: (...args) => { queries += 1; return inner.first(...args); },
      all: () => { queries += 1; return inner.all(); }, run: () => { queries += 1; return inner.run(); } });
    return wrap(db.prepare(sql));
  }, batch: (statements) => { queries += 1; return db.batch(statements.map((statement) => statement.inner)); } };
  await night.nightly({ ...bindings, DB: counted }, NIGHT);
  assert.ok(queries <= 25, `${queries} queries`);
});

test("the cron is daily, and the Worker hands it to the nightly job", async () => {
  const config = readFileSync(new URL("../api/wrangler.jsonc", import.meta.url), "utf8");
  assert.match(config, /"crons": \["17 3 \* \* \*"\]/);
  assert.match(config, /"binding": "STATS", "bucket_name": "firmware-liberator-stats"/);
  const { default: worker } = await import("../api/index.js");
  const waited = [];
  await worker.scheduled({ scheduledTime: NIGHT.getTime() }, env(), { waitUntil: (promise) => waited.push(promise) });
  assert.equal(waited.length, 1);
  assert.equal((await waited[0]).ran, true);
});

test("the month before January is December of the year before", () => {
  assert.equal(night.monthBefore("2027-01"), "2026-12");
  assert.equal(night.monthBefore("2026-11"), "2026-10");
});

test("a report held for review is in no total", async () => {
  const bindings = env();
  const db = bindings.DB;
  const report = { ...structuredClone(FIXTURE), id: randomBytes(16).toString("hex") };
  await db.batch(writes(db, report, null, "2026-11-09", "words no report would hold"));
  await night.nightly(bindings, NIGHT);
  assert.equal(rows(bindings, "SELECT * FROM daily_totals").length, 0);
});

test("each night notes how much the store of readouts holds", async () => {
  const bindings = env();
  await night.nightly(bindings, NIGHT);
  bindings.DB.db.prepare("INSERT INTO counters (name, day, count, bytes) VALUES (?, NULL, 3, 3000)")
    .run(night.READOUTS_EVER);
  await night.nightly(bindings, new Date("2026-11-11T03:17:00Z"));
  await night.nightly(bindings, new Date("2026-11-11T03:18:00Z"));
  assert.deepEqual(rows(bindings, "SELECT day, readouts, bytes FROM store_days ORDER BY day"), [
    { day: "2026-11-10", readouts: 0, bytes: 0 },
    { day: "2026-11-11", readouts: 3, bytes: 3000 },
  ]);
  const index = readFileSync(new URL("../api/index.js", import.meta.url), "utf8");
  assert.match(index, new RegExp(`const READOUTS_EVER = "${night.READOUTS_EVER}";`), "named alike in index.js");
});

test("each night clears readouts never taken as right and addresses never confirmed (pending.js)", async () => {
  const bindings = { ...env(), READOUTS: { ...bucket(), delete: async () => {} } };
  bindings.DB.db.prepare(`INSERT INTO readouts (id, key, stage, sender, fingerprint, bytes, object, received, review)
    VALUES ('old', 'K', 'whole', 's', 'f', 1, 'readouts/x/old.tar', '2026-09-01T00:00:00Z', 'pending')`).run();
  bindings.DB.db.prepare(`INSERT INTO signups (token, who, email, devices, day)
    VALUES ('t', 'w', 'e', '[]', '2026-10-01')`).run();
  const done = await night.nightly(bindings, NIGHT);
  assert.deepEqual(done.pruned, { readouts: 1, signups: 1 });
  assert.deepEqual(rows(bindings, "SELECT id FROM readouts"), []);
});
