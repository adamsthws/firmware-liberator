// The maintainer's emulator runs (emulator_runs, ../api/migrations): taken
// from the suites at RUNS_PATH, and shown on the admin view.
//
// The suites (tools/repo/emulator_stats.py) send what each scenario's run came
// to as each run ends, with Access's service token - a separate Access
// application covers SCOPE alone, whose one policy lets in that token and no
// one else (tools/repo/cloudflare_settings.py), so ACCESS_UPLOAD_AUD is its
// audience and no other. The token can send runs and do nothing else: the
// view itself refuses its JWT, and SCOPE refuses the maintainer's.
//
// Nothing here is a person's run: those are the API's reports, and no figure
// of theirs counts these.

import { esc, FULL, columns, heat, number, table, tile } from "./render.js";

// Every path the suites use, the runs' and any to come: what Access's
// application for them covers.
export const SCOPE = "/v1/emulator";
export const RUNS_PATH = `${SCOPE}/runs`;
// At most this many runs a request; the suites send more as several.
export const MAX_RUNS = 500;
// The largest body taken, ample for MAX_RUNS runs.
export const MAX_BODY = 1_000_000;
// A run is taken up to this long after it finished (the suites keep unsent
// ones until the next batch), and no later than a little after now.
const OLDEST_DAYS = 60;
const AHEAD_MS = 3_600_000;

// What a failure came from, as the maintainer marks it on the view: "tool",
// a bug in the tool, is what counts as a bug found.
export const EMULATOR_CAUSES = ["tool", "emulator", "cloud", "suite", "flaky"];

const SHORT = /^[\x20-\x7e]{1,64}$/;
// Each field a run carries, and what it may hold: every one is there, and no
// other. Null where a suite has nothing to say (an attended run has no fault).
const FIELDS = {
  id: (v) => typeof v === "string" && /^[0-9a-f]{32}$/.test(v),
  finished: (v) => typeof v === "string" && /^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$/.test(v) && !Number.isNaN(Date.parse(v)),
  batch: (v) => typeof v === "string" && /^[0-9a-f]{16}$/.test(v),
  suite: (v) => v === "fault" || v === "attended",
  // A chip family or device by key: any, so a new one needs no change here.
  family: (v) => typeof v === "string" && /^[a-z][a-z0-9-]{0,31}$/.test(v),
  device: (v) => typeof v === "string" && /^[a-z0-9][a-z0-9-]{0,47}$/.test(v),
  scenario: (v) => typeof v === "string" && /^[\x20-\x7e]{1,200}$/.test(v),
  route: (v) => v === null || (typeof v === "string" && SHORT.test(v)),
  step: (v) => v === null || (typeof v === "string" && SHORT.test(v)),
  fault: (v) => v === null || (typeof v === "string" && SHORT.test(v)),
  at: (v) => v === null || (Number.isInteger(v) && v >= 0 && v <= 100),
  verdict: (v) => typeof v === "string" && /^[A-Z][A-Z0-9-]{0,39}$/.test(v),
  tries: (v) => v === null || (Number.isInteger(v) && v >= 0 && v <= 99),
  seconds: (v) => v === null || (Number.isInteger(v) && v >= 0 && v <= 172_800),
  cloud: (v) => v === "stub" || v === "real" || v === "none",
  tool: (v) => typeof v === "string" && /^\d{1,4}\.\d{1,4}\.\d{1,4}$/.test(v),
  revision: (v) => v === null || (typeof v === "string" && /^[0-9a-f]{7,40}$/.test(v)),
  dirty: (v) => typeof v === "boolean",
  firmware: (v) => v !== null && typeof v === "object" && !Array.isArray(v) && Object.keys(v).length <= 16
    && Object.entries(v).every(([name, version]) => /^[a-z][a-z0-9-]{0,23}$/.test(name)
      && typeof version === "string" && /^[\x20-\x7e]{1,40}$/.test(version)),
  workers: (v) => v === null || (Number.isInteger(v) && v >= 1 && v <= 256),
};
const NAMES = Object.keys(FIELDS);

// Why `run` cannot be taken, or null.
export function refused(run, now) {
  if (run === null || typeof run !== "object" || Array.isArray(run)) {
    return "not a run";
  }
  const extra = Object.keys(run).filter((name) => !Object.hasOwn(FIELDS, name));
  if (extra.length) {
    return `fields no run has: ${extra.slice(0, 5).join(", ")}`;
  }
  const wrong = NAMES.filter((name) => !FIELDS[name](run[name]));
  if (wrong.length) {
    return `missing or not as a run has them: ${wrong.join(", ")}`;
  }
  const finished = Date.parse(run.finished);
  if (finished > now + AHEAD_MS || finished < now - OLDEST_DAYS * 86_400_000) {
    return `finished ${run.finished}: not within the last ${OLDEST_DAYS} days`;
  }
  return null;
}

const INSERT = `INSERT OR IGNORE INTO emulator_runs (${NAMES.join(", ")})
  VALUES (${NAMES.map((_, at) => `?${at + 1}`).join(", ")})`;

const json = (status, body) => new Response(JSON.stringify(body), {
  status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" } });

// POST RUNS_PATH: {"runs": [1 to MAX_RUNS]}, answered {"taken": [id...],
// "refused": [{"id", "why"}]}: a run already taken is taken again, so a suite
// that never heard the answer sends it once more and nothing is counted twice.
export async function upload(request, env, now = Date.now()) {
  if (request.headers.get("Content-Type") !== "application/json") {
    return json(415, { error: "runs are application/json" });
  }
  const body = await request.arrayBuffer();
  if (body.byteLength > MAX_BODY) {
    return json(413, { error: `at most ${MAX_BODY} bytes at once` });
  }
  let sent = null;
  try {
    sent = JSON.parse(new TextDecoder().decode(body));
  } catch {
    // below
  }
  const runs = sent?.runs;
  if (sent === null || typeof sent !== "object" || Object.keys(sent).join() !== "runs" || !Array.isArray(runs)
      || runs.length < 1 || runs.length > MAX_RUNS) {
    return json(400, { error: `that is not runs: {"runs": [1 to ${MAX_RUNS}]}` });
  }
  const taken = [];
  const refusedRuns = [];
  const statements = [];
  for (const run of runs) {
    const why = refused(run, now);
    if (why) {
      refusedRuns.push({ id: typeof run?.id === "string" ? run.id.slice(0, 32) : null, why });
      continue;
    }
    taken.push(run.id);
    statements.push(env.DB.prepare(INSERT).bind(...NAMES.map((name) => {
      const value = run[name];
      return name === "firmware" ? JSON.stringify(value) : value;
    })));
  }
  if (statements.length) {
    await env.DB.batch(statements);
  }
  return json(200, { taken, refused: refusedRuns });
}

// A failure's cause set, or cleared ("" for none), by run id: only from a
// form on the view (index.js checks that).
export async function setCause(form, env) {
  const id = String(form.get("id") ?? "");
  const cause = String(form.get("cause") ?? "");
  if (!/^[0-9a-f]{32}$/.test(id) || (cause !== "" && !EMULATOR_CAUSES.includes(cause))) {
    return false;
  }
  await env.DB.prepare("UPDATE emulator_runs SET cause = ?2 WHERE id = ?1 AND verdict != 'PASS'")
    .bind(id, cause || null).run();
  return true;
}

// How many days the view's figures go back.
export const EMULATOR_DAYS = 30;
// How many revisions the pass-rate table shows, newest first.
export const REVISIONS = 12;

// Each run with whether it is a new failure: one not passing where its
// scenario's run before it, on another revision, passed - what a change broke.
const WITH_BEFORE = `WITH ordered AS (
    SELECT *, lag(verdict) OVER (PARTITION BY family, device, scenario ORDER BY finished) AS before,
      lag(revision) OVER (PARTITION BY family, device, scenario ORDER BY finished) AS before_revision
    FROM emulator_runs)`;
const NEW_FAILURE = `(verdict != 'PASS' AND before = 'PASS' AND revision IS NOT before_revision)`;

// Everything the view's emulator section shows, in a fixed few queries.
export async function gatherEmulator(all, now) {
  const since = new Date(now.getTime() - EMULATOR_DAYS * 86_400_000).toISOString().slice(0, 10);
  const week = new Date(now.getTime() - 7 * 86_400_000).toISOString().slice(0, 10);
  const [totals] = await all(`SELECT count(*) AS runs, sum(verdict = 'BRICK') AS bricks,
      sum(finished >= ?1) AS week, sum(finished >= ?1 AND verdict = 'PASS') AS week_passed,
      sum(CASE WHEN finished >= ?1 THEN seconds ELSE 0 END) AS week_seconds,
      sum(verdict != 'PASS' AND cause IS NULL) AS unmarked, sum(cause = 'tool') AS bugs,
      count(DISTINCT batch) AS batches
    FROM emulator_runs`, week);
  return {
    totals,
    days: await all(`SELECT substr(finished, 1, 10) AS day, sum(verdict = 'PASS') AS passed,
        sum(verdict = 'BRICK') AS bricked, sum(verdict NOT IN ('PASS', 'BRICK')) AS failed
      FROM emulator_runs WHERE finished >= ? GROUP BY day ORDER BY day`, since),
    revisions: await all(`${WITH_BEFORE}
      SELECT revision, max(tool) AS tool, max(dirty) AS dirty, count(*) AS runs, sum(verdict = 'PASS') AS passed,
        sum(${NEW_FAILURE}) AS new_failures, count(DISTINCT scenario) AS scenarios, max(finished) AS last
      FROM ordered GROUP BY revision ORDER BY last DESC LIMIT ${REVISIONS}`),
    devices: await all(`SELECT family, device, suite, count(*) AS runs, sum(verdict = 'PASS') AS passed,
        count(DISTINCT scenario) AS scenarios, max(finished) AS last,
        (SELECT firmware FROM emulator_runs AS latest WHERE latest.family = e.family AND latest.device = e.device
          ORDER BY finished DESC LIMIT 1) AS firmware
      FROM emulator_runs AS e GROUP BY family, device, suite ORDER BY family, device, suite`),
    verdicts: await all(`SELECT verdict, count(*) AS n FROM emulator_runs WHERE finished >= ?
      GROUP BY verdict ORDER BY n DESC`, since),
    stepFault: await all(`SELECT step, fault, count(*) AS runs, sum(verdict != 'PASS') AS failed
      FROM emulator_runs WHERE suite = 'fault' AND step IS NOT NULL AND fault IS NOT NULL AND finished >= ?
      GROUP BY step, fault`, since),
    tries: await all(`SELECT step, sum(tries = 1) AS t1, sum(tries = 2) AS t2, sum(tries = 3) AS t3,
        sum(tries >= 4) AS t4, avg(seconds) AS seconds
      FROM emulator_runs WHERE suite = 'fault' AND verdict = 'PASS' AND step IS NOT NULL AND finished >= ?
      GROUP BY step ORDER BY step`, since),
    flaky: await all(`SELECT family, device, scenario, revision, sum(verdict = 'PASS') AS passed,
        sum(verdict != 'PASS') AS failed, max(finished) AS last
      FROM emulator_runs WHERE finished >= ? GROUP BY family, device, scenario, revision
      HAVING passed > 0 AND failed > 0 ORDER BY last DESC LIMIT 50`, since),
    // Real-cloud runs by the hour into their batch they finished in: eWeLink
    // drops sessions more the longer a batch goes on.
    cloudHours: await all(`SELECT CAST((julianday(finished) - julianday(first)) * 24 AS INTEGER) AS hour,
        count(*) AS runs, sum(verdict != 'PASS') AS failed
      FROM emulator_runs JOIN (SELECT batch AS b, min(finished) AS first FROM emulator_runs GROUP BY batch)
        ON b = batch
      WHERE cloud = 'real' AND finished >= ? GROUP BY hour ORDER BY hour`, since),
    causes: await all(`SELECT coalesce(cause, 'not marked') AS cause, count(*) AS n FROM emulator_runs
      WHERE verdict != 'PASS' GROUP BY cause ORDER BY n DESC`),
    failures: await all(`${WITH_BEFORE}
      SELECT id, finished, family, device, suite, scenario, verdict, tries, revision, tool, cause,
        ${NEW_FAILURE} AS new_failure
      FROM ordered WHERE verdict != 'PASS' ORDER BY finished DESC LIMIT 60`),
  };
}

const share = (part, of) => (of ? `${Math.round((100 * part) / of)}%` : "–");
const hours = (seconds) => `${number(Math.round((seconds ?? 0) / 360) / 10)} h`;
const firmwareSaid = (text) => {
  try {
    return Object.entries(JSON.parse(text ?? "{}")).map(([name, version]) => `${name} ${version}`).join(", ");
  } catch {
    return "";
  }
};

// The emulator section of the view: cards, from what gatherEmulator() read.
export function emulatorSections(data) {
  const t = data.totals ?? {};
  const steps = [...new Set(data.stepFault.map((row) => row.step))].sort();
  const faults = [...new Set(data.stepFault.map((row) => row.fault))].sort();
  const cell = new Map(data.stepFault.map((row) => [`${row.step}\n${row.fault}`, row]));
  const causeForm = (row) => `<form class="inline" method="post" action="/emulator-cause">`
    + `<input type="hidden" name="id" value="${esc(row.id)}"><select name="cause" aria-label="Cause">`
    + [["", "-"], ...EMULATOR_CAUSES.map((cause) => [cause, cause])].map(([value, label]) =>
      `<option value="${value}"${(row.cause ?? "") === value ? " selected" : ""}>${label}</option>`).join("")
    + `</select> <button>Mark</button></form>`;
  return [
    `<section class="card" id="emulator"><h2>Emulator runs</h2><p class="sub">The maintainer's own suites, fault
      and attended, every chip family: one row a scenario run, sent by the suites at the end of a batch. No person's
      run is among them.</p><div class="tiles">`
      + tile("Runs", number(t.runs), `${number(t.batches)} batches`)
      + tile("The last 7 days", number(t.week), `${share(t.week_passed, t.week)} passed`)
      + tile("Emulator time, 7 days", hours(t.week_seconds))
      + tile("Bricked", number(t.bricks ?? 0), "should stay 0")
      + tile("Bugs found", number(t.bugs ?? 0), `failures marked "tool" · ${share(t.bugs, t.runs)} of runs`)
      + tile("Failures not marked", number(t.unmarked ?? 0))
      + `</div>`
      + columns(data.days.map((row) => ({ day: row.day, parts: { passed: row.passed, failed: row.failed, bricked: row.bricked } })),
        ["passed", "failed", "bricked"], "Emulator runs a day", FULL)
      + `</section>`,
    `<section class="card" id="emulator-revisions"><h2>By revision</h2><p class="sub">The newest ${REVISIONS}
      checkouts the suites ran, newest first. A new failure is a scenario that passed on the revision before and
      does not on this one: what a change broke.</p>`
      + table([["Revision", (row) => (row.revision ?? "none") + (row.dirty ? " (changed)" : "")], ["Tool", "tool"],
        ["Runs", "runs", "number"], ["Scenarios", "scenarios", "number"], ["Passed", (row) => share(row.passed, row.runs)],
        ["New failures", "new_failures", "number"], ["Last", (row) => String(row.last).slice(0, 16)]],
      data.revisions, "No runs yet.")
      + `</section>`,
    `<section class="card" id="emulator-devices"><h2>By device</h2><p class="sub">Every device and suite run,
      and the firmware its newest run installed: a firmware release no run has met shows here first.</p>`
      + table([["Family", "family"], ["Device", "device"], ["Suite", "suite"], ["Runs", "runs", "number"],
        ["Scenarios", "scenarios", "number"], ["Passed", (row) => share(row.passed, row.runs)],
        ["Last", (row) => String(row.last).slice(0, 10)], ["Firmware", (row) => firmwareSaid(row.firmware)]],
      data.devices, "No runs yet.")
      + `</section>`,
    `<section class="card" id="emulator-faults"><h2>Fault by step</h2><p class="sub">The last ${EMULATOR_DAYS} days'
      fault-suite runs: those failed of those run, at each step and fault. The darker, the more failed.</p>`
      + heat(steps, faults, (step, fault) => {
        const row = cell.get(`${step}\n${fault}`);
        return row ? { share: row.failed / row.runs, text: `${row.failed}/${row.runs}` } : null;
      }, "Step", "None run yet.")
      + `</section>`,
    `<div class="grid2"><section class="card" id="emulator-tries"><h2>Tries to convert</h2><p class="sub">Passing
      fault-suite runs by step: how many runs of the tool the device took to convert after its fault, and how long,
      the last ${EMULATOR_DAYS} days. More than one works, but is more for a person to sit through.</p>`
      + table([["Step", "step"], ["1", "t1", "number"], ["2", "t2", "number"], ["3", "t3", "number"],
        ["4+", "t4", "number"], ["Seconds", (row) => number(Math.round(row.seconds ?? 0))]], data.tries, "None yet.")
      + `</section>`,
    `<section class="card" id="emulator-verdicts"><h2>Verdicts</h2><p class="sub">The last ${EMULATOR_DAYS} days.
      A MISTIMED one is the suite's, not the tool's.</p>`
      + table([["Verdict", "verdict"], ["Runs", "n", "number"]], data.verdicts, "None yet.")
      + table([["Failures from", "cause"], ["Runs", "n", "number"]], data.causes, "No failures.")
      + `</section></div>`,
    `<div class="grid2"><section class="card" id="emulator-flaky"><h2>Flaky</h2><p class="sub">Scenarios that both
      passed and failed on one revision, the last ${EMULATOR_DAYS} days.</p>`
      + table([["Device", "device"], ["Scenario", "scenario", "text"], ["Revision", "revision"],
        ["Passed", "passed", "number"], ["Failed", "failed", "number"]], data.flaky, "None.")
      + `</section>`,
    `<section class="card" id="emulator-cloud"><h2>Real cloud, by hour</h2><p class="sub">Runs on eWeLink's own
      cloud by the hour into their batch, the last ${EMULATOR_DAYS} days: failures climbing late are eWeLink's
      limits, not the tool.</p>`
      + table([["Hour", "hour", "number"], ["Runs", "runs", "number"], ["Failed", "failed", "number"],
        ["", (row) => share(row.failed, row.runs)]], data.cloudHours, "No real-cloud runs.")
      + `</section></div>`,
    `<section class="card" id="emulator-failures"><h2>Failures</h2><p class="sub">The newest 60. Mark what each
      came from: "tool" counts as a bug found.</p>`
      + table([["Finished", (row) => String(row.finished).slice(0, 16)], ["Device", "device"], ["Suite", "suite"],
        ["Scenario", "scenario", "text"], ["Verdict", (row) => row.verdict + (row.new_failure ? " (new)" : "")],
        ["Tries", "tries", "number"], ["Revision", "revision"], ["Cause", causeForm, "html"]],
      data.failures, "No failures.")
      + `</section>`,
  ];
}
