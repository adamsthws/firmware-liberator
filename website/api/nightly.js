// The API Worker's nightly job (wrangler.jsonc's cron): what is kept of the
// reports for good, then the public figures.
// - Each day before today not yet counted is folded into daily_totals, once
//   (folded); then every month with a day folded tonight, and the current and
//   previous months, are made again: monthly_totals, and the figures a total
//   cannot give (run and step times, computers, Sonoff firmware met).
// - Reports RAW_DAYS old are deleted, once their day is folded; the day
//   counts behind the limits (counters) COUNTER_DAYS old.
// - The store of readouts' size is noted for the day (store_days), for the
//   admin view's graph of how fast it fills.
// - The public snapshot is written to the stats bucket: totals only, from
//   the maintainer's own testing and quarantined reports left out, and no
//   figure for fewer than SMALLEST reports or devices (snapshot.js).
// The work is done in SQL, on the database's side: the free plan gives a
// Worker 10 ms of its own time, and the job's queries are a fixed few.
import { ready } from "./database.js";
import { snapshot } from "./snapshot.js";

export const RAW_DAYS = 365;
export const COUNTER_DAYS = 7;
export const SNAPSHOT_KEY = "v1/stats.json";
export const ARCHIVE_PREFIX = "v1/archive/";
// The count of every readout kept, as index.js names it (READOUTS_EVER; a
// test holds the two alike).
export const READOUTS_EVER = "readouts:all";

const DAY = 86_400_000;
export const dayOf = (date) => date.toISOString().slice(0, 10);
const daysBefore = (day, days) => dayOf(new Date(Date.parse(`${day}T00:00:00Z`) - days * DAY));
export function monthBefore(month) {
  const [year, number] = month.split("-").map(Number);
  return number === 1 ? `${year - 1}-12` : `${year}-${String(number - 1).padStart(2, "0")}`;
}

// The columns each total is kept by: a report's own, '' for a value it had none of.
const BY = "tag, tool, device, model, firmware, source, result, step, error";
const REPORTED = `tag, tool, coalesce(device, ''), coalesce(model, ''), coalesce(firmware, ''), source, result,
  coalesce(step, ''), coalesce(error, '')`;

// Days before `?1` with reports, not yet folded.
const UNFOLDED = "SELECT DISTINCT day FROM reports WHERE day < ?1 AND day NOT IN (SELECT day FROM folded)";

// The statements folding every unfolded day: its totals made afresh (a day
// unfolded by hand may have some), then marked folded.
function folding(db, today) {
  return [
    db.prepare(`DELETE FROM daily_totals WHERE day IN (${UNFOLDED})`).bind(today),
    db.prepare(`INSERT INTO daily_totals (day, ${BY}, n)
      SELECT day, ${REPORTED}, count(*) FROM reports
      WHERE quarantined = 0 AND day IN (${UNFOLDED})
      GROUP BY day, ${BY}`).bind(today),
    db.prepare(`INSERT OR IGNORE INTO folded (day) ${UNFOLDED}`).bind(today),
  ];
}

// The median and 90th percentile of `value` over each group of `rows` (a
// subquery giving `value` and the group's columns, `by`): in SQL, by rank.
const percentiles = (rows, by) => `SELECT ${by}, max(n), max(CASE WHEN rank = (n + 1) / 2 THEN value END),
    max(CASE WHEN rank = (9 * n + 9) / 10 THEN value END)
  FROM (SELECT *, row_number() OVER (PARTITION BY ${by} ORDER BY value) AS rank,
          count(*) OVER (PARTITION BY ${by}) AS n FROM (${rows}))
  GROUP BY ${by}`;

// The statements making each of `months` again: its totals from daily_totals,
// and its other figures from the reports of its days before today.
function remaking(db, months, today) {
  // Each statement is given today as ?1 and the months as ?2 on; one that
  // needs no ?1 leaves it unused, which SQLite allows.
  const marks = months.map((_, index) => `?${index + 2}`).join(", ");
  const of = (sql) => db.prepare(sql).bind(today, ...months);
  const inMonths = `substr(day, 1, 7) IN (${marks})`;
  const reports = `reports WHERE quarantined = 0 AND day < ?1 AND ${inMonths}`;
  const tables = ["monthly_totals", "monthly_times", "monthly_steps", "monthly_environment", "monthly_stock"];
  return [
    ...tables.map((table) => of(`DELETE FROM ${table} WHERE month IN (${marks})`)),
    of(`INSERT INTO monthly_totals (month, ${BY}, n)
      SELECT substr(day, 1, 7), ${BY}, sum(n) FROM daily_totals WHERE ${inMonths}
      GROUP BY substr(day, 1, 7), ${BY}`),
    of(`INSERT INTO monthly_times (month, tag, device, firmware, source, n, p50, p90)
      ${percentiles(`SELECT substr(day, 1, 7) AS month, tag, coalesce(device, '') AS device,
        coalesce(firmware, '') AS firmware, source, seconds AS value FROM ${reports} AND result = 'converted'`,
        "month, tag, device, firmware, source")}`),
    of(`INSERT INTO monthly_steps (month, tag, tool, step, n, p50, p90)
      ${percentiles(`SELECT substr(day, 1, 7) AS month, tag, tool, step.key AS step,
        json_extract(step.value, '$.s') AS value FROM reports, json_each(reports.stages) AS step
        WHERE quarantined = 0 AND day < ?1 AND ${inMonths}`, "month, tag, tool, step")}`),
    of(`INSERT INTO monthly_environment (month, tag, os_family, python_source, n)
      SELECT substr(day, 1, 7), tag, os_family, python_source, count(*) FROM ${reports}
      GROUP BY substr(day, 1, 7), tag, os_family, python_source`),
    of(`INSERT INTO monthly_stock (month, tag, model, stock, runs, converted)
      SELECT substr(day, 1, 7), tag, model, stock, count(*), sum(result = 'converted') FROM ${reports}
        AND model IS NOT NULL AND stock IS NOT NULL
      GROUP BY substr(day, 1, 7), tag, model, stock`),
  ];
}

// The statements deleting what is kept no longer: reports RAW_DAYS old, but
// only of a day already counted, whatever order the job runs in; and the
// day counts behind the limits, COUNTER_DAYS old.
export function pruning(db, today) {
  return [
    db.prepare("DELETE FROM reports WHERE day < ? AND day IN (SELECT day FROM folded)").bind(daysBefore(today, RAW_DAYS)),
    db.prepare("DELETE FROM counters WHERE day IS NOT NULL AND day < ?").bind(daysBefore(today, COUNTER_DAYS)),
  ];
}

export async function nightly(env, now = new Date()) {
  if (!(await ready(env.DB))) {
    return { ran: false, why: "the database does not have every migration yet" };
  }
  const today = dayOf(now);
  const { results } = await env.DB.prepare(UNFOLDED).bind(today).all();
  const thisMonth = today.slice(0, 7);
  const months = [...new Set([thisMonth, monthBefore(thisMonth), ...results.map((row) => row.day.slice(0, 7))])]
    .sort();
  await env.DB.batch([...folding(env.DB, today), ...remaking(env.DB, months, today)]);
  await env.DB.batch([...pruning(env.DB, today),
    env.DB.prepare(`INSERT INTO store_days (day, readouts, bytes)
      SELECT ?1, coalesce(max(count), 0), coalesce(max(bytes), 0) FROM counters WHERE name = ?2
      ON CONFLICT (day) DO UPDATE SET readouts = excluded.readouts, bytes = excluded.bytes`).bind(today, READOUTS_EVER)]);
  const figures = await snapshot(env.DB, now);
  const text = JSON.stringify(figures);
  const options = { httpMetadata: { contentType: "application/json", cacheControl: "public, max-age=3600" } };
  await env.STATS.put(SNAPSHOT_KEY, text, options);
  // One kept a month, the first of it written, so the figures' history stays.
  const archive = `${ARCHIVE_PREFIX}${thisMonth}.json`;
  if (!(await env.STATS.head(archive))) {
    await env.STATS.put(archive, text, options);
  }
  return { ran: true, folded: results.length, months };
}
