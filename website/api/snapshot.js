// The public figures the nightly job writes (stats.json, CC BY 4.0), which
// the website's /stats page is built from: totals only, never a report.
// - The maintainer's own testing (tag "dev", and devices it has seen) and
//   quarantined reports are left out.
// - No figure stands for fewer than SMALLEST: a count below it is null, a
//   rate is given only over SMALLEST or more, and a row of a list (a model,
//   a Sonoff version, a tool version) under it is left out, so no one report
//   or device can be picked out, nor a made-up name shown by a few reports.
// - A run's success is a conversion, over conversions, failures and crashes:
//   one the person declined, or with nothing to do, counts apart.
import { LATEST_MIGRATION } from "./database.js";

export const FORMAT = 1;
export const SMALLEST = 5;
// Months of run times given, the latest first.
export const TIME_MONTHS = 12;
const NOT_FAILURES = "('declined', 'nothing-to-do')";

const count = (n) => (Number.isInteger(n) && n >= SMALLEST ? n : null);
// A percentage to one decimal, of `whole` SMALLEST or more.
const rate = (part, whole) => (whole >= SMALLEST ? Math.round((1000 * part) / whole) / 10 : null);

// sum(n) of monthly_totals' public rows as runs: what ended how.
const OUTCOMES = `sum(n) AS runs,
  sum(CASE WHEN result = 'converted' THEN n ELSE 0 END) AS converted,
  sum(CASE WHEN result = 'failed' AND error NOT IN ${NOT_FAILURES} THEN n ELSE 0 END) AS failed,
  sum(CASE WHEN result = 'crashed' THEN n ELSE 0 END) AS crashed,
  sum(CASE WHEN result = 'failed' AND error IN ${NOT_FAILURES} THEN n ELSE 0 END) AS declined,
  sum(CASE WHEN result = 'updated' THEN n ELSE 0 END) AS updated,
  sum(CASE WHEN result = 'put-back' THEN n ELSE 0 END) AS put_back`;

const success = (row) => rate(row.converted, row.converted + row.failed + row.crashed);

function outcomes(row) {
  return {
    runs: count(row.runs), converted: count(row.converted), failed: count(row.failed), crashed: count(row.crashed),
    declined: count(row.declined), updated: count(row.updated), putBack: count(row.put_back), success: success(row),
  };
}

export async function snapshot(db, now = new Date()) {
  const all = async (sql, ...values) => (await db.prepare(sql).bind(...values).all()).results;
  const today = now.toISOString().slice(0, 10);
  const through = new Date(Date.parse(`${today}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
  const [devices] = await all(`SELECT count(*) AS seen, sum(conversions > 0) AS converted,
    sum(first_result = 'converted') AS first_run, sum(back_to_sonoff > 0) AS back FROM plugs WHERE dev = 0`);
  const [median] = await all(`SELECT runs_to_convert AS value FROM (SELECT runs_to_convert,
      row_number() OVER (ORDER BY runs_to_convert) AS rank, count(*) OVER () AS n
    FROM plugs WHERE dev = 0 AND runs_to_convert IS NOT NULL) WHERE rank = (n + 1) / 2`);
  const [runs] = await all(`SELECT ${OUTCOMES} FROM monthly_totals WHERE tag = 'user'`);
  const months = await all(`SELECT month, ${OUTCOMES} FROM monthly_totals WHERE tag = 'user'
    GROUP BY month ORDER BY month`);
  const newDevices = new Map((await all(`SELECT substr(first_day, 1, 7) AS month, count(*) AS n FROM plugs
    WHERE dev = 0 GROUP BY month`)).map((row) => [row.month, row.n]));
  const models = await all(`SELECT model, max(device) AS device, count(*) AS devices,
      sum(conversions > 0) AS converted, sum(first_result = 'converted') AS first_run
    FROM plugs WHERE dev = 0 AND model IS NOT NULL GROUP BY model HAVING count(*) >= ?
    ORDER BY devices DESC, model`, SMALLEST);
  const modelMonths = await all(`SELECT substr(first_day, 1, 7) AS month, model, count(*) AS devices FROM plugs
    WHERE dev = 0 AND model IS NOT NULL GROUP BY month, model HAVING count(*) >= ? ORDER BY month, model`, SMALLEST);
  const routes = await all(`SELECT source, firmware, ${OUTCOMES} FROM monthly_totals
    WHERE tag = 'user' AND firmware != '' GROUP BY source, firmware HAVING sum(n) >= ? ORDER BY runs DESC`, SMALLEST);
  const tools = await all(`SELECT tool, ${OUTCOMES} FROM monthly_totals WHERE tag = 'user'
    GROUP BY tool HAVING sum(n) >= ? ORDER BY tool`, SMALLEST);
  const failures = await all(`SELECT step, error, sum(n) AS runs FROM monthly_totals
    WHERE tag = 'user' AND result IN ('failed', 'crashed') AND error NOT IN ${NOT_FAILURES}
    GROUP BY step, error HAVING sum(n) >= ? ORDER BY runs DESC, step, error`, SMALLEST);
  const unsupported = new Map((await all(`SELECT model, sum(n) AS n FROM monthly_totals
    WHERE tag = 'user' AND error = 'unsupported-device' AND model != '' GROUP BY model`))
    .map((row) => [row.model, row.n]));
  const asked = await all("SELECT model, sum(asked) AS asked FROM wanted_days GROUP BY model");
  const wanted = new Map(asked.map((row) => [row.model, { asked: row.asked, unsupported: 0 }]));
  for (const [model, n] of unsupported) {
    wanted.set(model, { asked: wanted.get(model)?.asked ?? 0, unsupported: n });
  }
  const stock = await all(`SELECT model, stock, sum(runs) AS runs, sum(converted) AS converted FROM monthly_stock
    WHERE tag = 'user' GROUP BY model, stock HAVING sum(runs) >= ? ORDER BY model, stock`, SMALLEST);
  const computers = await all(`SELECT os_family, python_source, sum(n) AS runs FROM monthly_environment
    WHERE tag = 'user' GROUP BY os_family, python_source HAVING sum(n) >= ? ORDER BY runs DESC`, SMALLEST);
  const times = await all(`SELECT month, device, firmware, source, n, p50, p90 FROM monthly_times
    WHERE tag = 'user' AND n >= ? AND month IN (SELECT DISTINCT month FROM monthly_times ORDER BY month DESC LIMIT ?)
    ORDER BY month DESC, n DESC`, SMALLEST, TIME_MONTHS);
  return {
    format: FORMAT,
    generated: now.toISOString().slice(0, 16) + "Z",
    through,
    schema: LATEST_MIGRATION,
    licence: "CC BY 4.0",
    attribution: "FirmwareLiberator, https://firmwareliberator.com/stats",
    smallest: SMALLEST,
    devices: {
      seen: count(devices.seen), converted: count(devices.converted ?? 0), backToSonoff: count(devices.back ?? 0),
      firstRun: rate(devices.first_run ?? 0, devices.seen), eventually: rate(devices.converted ?? 0, devices.seen),
      medianRuns: (devices.converted ?? 0) >= SMALLEST ? (median?.value ?? null) : null,
    },
    runs: outcomes(runs),
    months: months.map((row) => ({ month: row.month, newDevices: count(newDevices.get(row.month)), ...outcomes(row) })),
    models: models.map((row) => ({
      model: row.model, device: row.device || null, devices: row.devices, converted: count(row.converted),
      firstRun: rate(row.first_run, row.devices), eventually: rate(row.converted, row.devices),
    })),
    modelMonths: modelMonths.map((row) => ({ month: row.month, model: row.model, newDevices: row.devices })),
    wanted: [...wanted].filter(([, row]) => row.asked + row.unsupported >= SMALLEST)
      .sort(([a, x], [b, y]) => (y.asked + y.unsupported) - (x.asked + x.unsupported) || a.localeCompare(b))
      .map(([model, row]) => ({ model, asked: count(row.asked), unsupportedRuns: count(row.unsupported) })),
    routes: routes.map((row) => ({ from: row.source, firmware: row.firmware, ...outcomes(row) })),
    tools: tools.map((row) => ({ tool: row.tool, ...outcomes(row) })),
    failures: failures.map((row) => ({ step: row.step || null, error: row.error, runs: row.runs })),
    stock: stock.map((row) => ({ model: row.model, stock: row.stock, runs: row.runs, converted: count(row.converted),
      success: rate(row.converted, row.runs) })),
    computers: computers.map((row) => ({ os: row.os_family, python: row.python_source, runs: row.runs })),
    times: times.map((row) => ({ month: row.month, device: row.device || null, firmware: row.firmware || null,
      from: row.source, runs: row.n, median: row.p50, p90: row.p90 })),
  };
}
