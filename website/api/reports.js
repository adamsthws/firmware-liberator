// /reports: the reports the tool sends at the end of each run, unless told not
// to (tools/autoflash/report_data.py makes them), kept for the project's own
// figures: how many devices it has converted, and how reliably, by device.
//
// POST {"reports": [<report>, ...]} (application/json): the run's report, with
// any the tool could not send before. Each is held to report-schema.json, and
// carries its own proof of work, made for its id before the report existed,
// so the tool does the work while the run goes on. The answer is always
// {"accepted", "duplicate", "rejected": [{"id", "why"}], "later"}: the tool
// forgets the first three, and sends the rest again another time.
//
// Kept, and how floods are bounded:
// - Nothing of the sender: no address (the rate limiter holds it a minute,
//   never stored), no time of day; a report's day, as a UTC date.
// - The device as a keyed hash (PLUG_KEY, a Worker secret) of the hash the
//   tool sends of its MAC, so the stored value leads back to no device
//   without the key, which never leaves Cloudflare.
// - At most REPORTS_DAILY a day from everyone, and PLUG_DAILY a day for one
//   device; past the first, the rest wait for another day ("later").
// - Ingesting stops (503) while INGEST is not "on" in wrangler.jsonc.
// - One no run of the tool's would write (review.js) is held for review:
//   answered as accepted, kept quarantined, counted nowhere.
// - A request costs a fixed few database queries however many reports it
//   carries (D1 allows 50 a request on the free plan): the duplicates, the
//   devices' counts for the day, the day's count, and one batch to write.
import { answer } from "./answer.js";
import { release, reserve, reserveUpTo } from "./counters.js";
import { hex, proven, reportSubject, work } from "./proof-of-work.js";
import schema from "./report-schema.json" with { type: "json" };
import { validate } from "./report-schema.js";
import { heldFor } from "./review.js";

export const MAX_BODY = 1024 * 1024;
export const MAX_REPORTS = 99;
export const REPORTS_DAILY = 1000;
export const PLUG_DAILY = 20;
export const REPORT_PROOF_BITS = 24;
// A report is sent when its run ends, or kept and sent within 30 days.
export const REPORT_PROOF_AGE = 31 * 86400;
// How many of /wanted's questions a day are counted, at most.
export const WANTED_COUNTED = 2000;
const MODEL = new RegExp(schema.properties.model.pattern);

const ID = /^[0-9a-f]{32}$/;
const reportsOn = (day) => `reports:${day}`;

// The plug as stored: HMAC-SHA256 of the tool's hash under PLUG_KEY.
const keys = new Map();
async function keyed(secret, plug) {
  if (!keys.has(secret)) {
    keys.set(secret, await crypto.subtle.importKey("raw", new TextEncoder().encode(secret),
      { name: "HMAC", hash: "SHA-256" }, false, ["sign"]));
  }
  return hex(new Uint8Array(await crypto.subtle.sign("HMAC", keys.get(secret), new TextEncoder().encode(plug))));
}

// Why `report` is turned away, or null.
async function problem(report, now, bits) {
  if (new TextEncoder().encode(JSON.stringify(report)).length > schema.maxBytes) {
    return `larger than ${schema.maxBytes} bytes`;
  }
  const [first] = validate(schema, report);
  if (first) {
    return first;
  }
  if (!(await proven(report.proof, reportSubject(report.id), now.getTime() / 1000, bits, REPORT_PROOF_AGE))) {
    return "its proof of work does not check out";
  }
  return null;
}

const INSERT = `INSERT OR IGNORE INTO reports (id, day, tag, tool, kind, result, step, error, text, family, device,
  model, stock, plug, firmware, source, backup, identity, os_family, os_version, python_version, python_source,
  seconds, resumed, stages, signal, wifi_typed, setup_network, power_cycled, checks, meter, quarantined)
  VALUES (${Array(32).fill("?").join(", ")})`;
const HOLD = "INSERT OR IGNORE INTO held_reports (id, day, why) VALUES (?, ?, ?)";

// The device's record, brought up to date by one report: only where that
// report was written just before (changes(), in the same transaction), so a
// report sent twice at the same moment counts once.
const PLUG = `INSERT INTO plugs (plug, model, device, first_day, last_day, first_result, converted_day, runs,
    runs_to_convert, conversions, back_to_sonoff, firmware, stock, dev)
  SELECT ?1, ?2, ?3, ?4, ?4, ?5, CASE WHEN ?6 THEN ?4 END, 1, CASE WHEN ?6 THEN 1 END, ?6, ?7,
    CASE WHEN ?6 OR ?7 THEN ?8 END, ?9, ?10
  WHERE changes() = 1
  ON CONFLICT (plug) DO UPDATE SET
    model = coalesce(excluded.model, plugs.model),
    device = coalesce(excluded.device, plugs.device),
    last_day = max(plugs.last_day, excluded.last_day),
    runs = plugs.runs + 1,
    runs_to_convert = coalesce(plugs.runs_to_convert, CASE WHEN excluded.conversions = 1 THEN plugs.runs + 1 END),
    converted_day = coalesce(plugs.converted_day, excluded.converted_day),
    conversions = plugs.conversions + excluded.conversions,
    back_to_sonoff = plugs.back_to_sonoff + excluded.back_to_sonoff,
    firmware = coalesce(excluded.firmware, plugs.firmware),
    stock = coalesce(plugs.stock, excluded.stock),
    dev = max(plugs.dev, excluded.dev)`;

const flag = (value) => (value === null ? null : Number(value));

// The statements that write one report, with its device's record, or for
// one held (`held`: why), only it, quarantined, and why it is held.
export function writes(db, report, plug, day, held = null) {
  const stopped = report.stopped ?? {};
  const statements = [db.prepare(INSERT).bind(
    report.id, day, report.tag, report.tool, report.kind, report.result, stopped.step ?? null,
    stopped.error ?? null, stopped.text ?? null, report.family, report.device, report.model, report.stock, plug,
    report.firmware, report.from, report.backup, report.identity, report.os.family, report.os.version,
    report.python.version, report.python.source, report.seconds, Number(report.resumed),
    JSON.stringify(report.stages), report.signal, flag(report.wifi_typed), flag(report.setup_network),
    flag(report.power_cycled), report.checks, report.meter, Number(held !== null))];
  if (held !== null) {
    return [...statements, db.prepare(HOLD).bind(report.id, day, held)];
  }
  if (plug !== null) {
    // A conversion is to another firmware than Sonoff's; back to Sonoff's is counted apart.
    const converted = report.result === "converted" && report.firmware !== "sonoff";
    const backToSonoff = report.result === "converted" && report.firmware === "sonoff";
    statements.push(db.prepare(PLUG).bind(plug, report.model, report.device, day, report.result, Number(converted),
      Number(backToSonoff), report.firmware, report.stock, Number(report.tag === "dev")));
  }
  return statements;
}

export async function reports(request, env, now = new Date()) {
  if (request.method !== "POST") {
    return answer(405, { error: "reports are sent here by the tool, with POST" }, { Allow: "POST" });
  }
  if (env.INGEST !== "on" || !env.PLUG_KEY) {
    return answer(503, { error: "reports are not being taken just now: try again later" }, { "Retry-After": "3600" });
  }
  if (request.headers.get("Content-Type") !== "application/json") {
    return answer(415, { error: "reports are application/json" });
  }
  const declared = Number(request.headers.get("Content-Length"));
  if (!Number.isInteger(declared) || declared <= 0) {
    return answer(411, { error: "reports say their length" });
  }
  if (declared > MAX_BODY) {
    return answer(413, { error: `reports are at most ${MAX_BODY} bytes at once` });
  }
  const { success } = await env.REPORT_LIMIT.limit({ key: request.headers.get("CF-Connecting-IP") || "unknown" });
  if (!success) {
    return answer(429, { error: "too many at once: try again in a minute" });
  }
  const body = await request.arrayBuffer();
  if (body.byteLength !== declared) {
    return answer(400, { error: "those are not whole reports" });
  }
  let sent = null;
  try {
    sent = JSON.parse(new TextDecoder().decode(body));
  } catch {
    // below
  }
  const list = sent?.reports;
  if (sent === null || typeof sent !== "object" || Array.isArray(sent) || Object.keys(sent).join() !== "reports"
      || !Array.isArray(list) || list.length < 1 || list.length > MAX_REPORTS) {
    return answer(400, { error: `that is not reports this tool sends: {"reports": [1 to ${MAX_REPORTS}]}` });
  }
  const day = now.toISOString().slice(0, 10);
  const outcome = { accepted: [], duplicate: [], rejected: [], later: [] };
  const bits = work.bits ?? REPORT_PROOF_BITS;
  const valid = [];
  for (const report of list) {
    const id = typeof report?.id === "string" && ID.test(report.id) ? report.id : null;
    const why = await problem(report, now, bits);
    if (why) {
      outcome.rejected.push({ id, why });
    } else if (valid.some((other) => other.id === id)) {
      outcome.duplicate.push(id);
    } else {
      valid.push(report);
    }
  }
  if (!valid.length) {
    return answer(200, outcome);
  }
  const marks = valid.map(() => "?").join(", ");
  const { results: held } = await env.DB.prepare(`SELECT id FROM reports WHERE id IN (${marks})`)
    .bind(...valid.map((report) => report.id)).all();
  const known = new Set(held.map((row) => row.id));
  outcome.duplicate.push(...valid.filter((report) => known.has(report.id)).map((report) => report.id));
  const fresh = valid.filter((report) => !known.has(report.id));
  const plugs = await Promise.all(fresh.map((report) => (report.plug ? keyed(env.PLUG_KEY, report.plug) : null)));
  // Each device's reports today, and those this request adds, held to PLUG_DAILY.
  const named = [...new Set(plugs.filter(Boolean))];
  const today = new Map();
  if (named.length) {
    const { results } = await env.DB.prepare(
      `SELECT plug, COUNT(*) AS n FROM reports WHERE day = ? AND plug IN (${named.map(() => "?").join(", ")})
       GROUP BY plug`).bind(day, ...named).all();
    for (const row of results) {
      today.set(row.plug, row.n);
    }
  }
  const taking = [];
  fresh.forEach((report, index) => {
    const plug = plugs[index];
    if (plug !== null && (today.get(plug) ?? 0) >= PLUG_DAILY) {
      outcome.rejected.push({ id: report.id, why: `more than ${PLUG_DAILY} reports of one device in a day` });
      return;
    }
    if (plug !== null) {
      today.set(plug, (today.get(plug) ?? 0) + 1);
    }
    taking.push([report, plug]);
  });
  const granted = taking.length ? await reserveUpTo(env.DB, reportsOn(day), day, taking.length, REPORTS_DAILY) : 0;
  outcome.later.push(...taking.slice(granted).map(([report]) => report.id));
  const kept = taking.slice(0, granted);
  if (kept.length) {
    try {
      await env.DB.batch(kept.flatMap(([report, plug]) => writes(env.DB, report, plug, day, heldFor(report))));
    } catch (error) {
      await release(env.DB, reportsOn(day), 0, kept.length);
      throw error;
    }
    outcome.accepted.push(...kept.map(([report]) => report.id));
  }
  return answer(200, outcome);
}

// One of /wanted's questions, counted for the figures by its product model
// and day (wanted_days): the devices people would read out for the tool to
// learn. Only a model's shape is counted, and WANTED_COUNTED a day, so a
// flood of made-up names cannot grow the table without end.
export async function countWanted(db, product, day) {
  const model = String(product).trim().toUpperCase();
  if (!MODEL.test(model) || !(await reserve(db, `wanted:${day}`, day, { most: WANTED_COUNTED }))) {
    return;
  }
  await db.prepare(`INSERT INTO wanted_days (day, model, asked) VALUES (?, ?, 1)
    ON CONFLICT (day, model) DO UPDATE SET asked = asked + 1`).bind(day, model).run();
}
