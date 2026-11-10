// The maintainer's email from the nightly job (nightly.js), when something
// needs looking at: yesterday's limit for everyone reached - of readouts, by
// count or by bytes, of reports, or of addresses given - or the store of
// readouts past STORE_WARN of its limit, the night it passes it. Counts only:
// never a report, a readout, an address or anything of a sender. Sent with the
// Worker's EMAIL binding (Cloudflare Email Service) to ALERTS_RECIPIENT_EMAIL, a secret set
// by tools/repo/cloudflare_settings.py apply from the maintainer's Email
// Routing destination; with either missing, nothing is sent.
import { read } from "./counters.js";

export const STORE_WARN = 0.75;

const DAY = 86_400_000;
const dayBefore = (day) => new Date(Date.parse(`${day}T00:00:00Z`) - DAY).toISOString().slice(0, 10);
const megabytes = (bytes) => `${Math.round(bytes / 1024 / 1024)} MB`;

// What to say, as lines: [] when all is well. `limits` as index.js gives
// them (its scheduled()): DAILY_COUNT, DAILY_BYTES, TOTAL_BYTES, NOTIFY_DAILY,
// and REPORTS_DAILY.
export async function alertLines(db, today, limits) {
  const yesterday = dayBefore(today);
  const lines = [];
  const readouts = await read(db, `readouts:${yesterday}`);
  if (readouts.count >= limits.DAILY_COUNT || readouts.bytes >= limits.DAILY_BYTES) {
    lines.push(`The day's limit of readouts was reached on ${yesterday}: ${readouts.count} of ${limits.DAILY_COUNT}, `
      + `${megabytes(readouts.bytes)} of ${megabytes(limits.DAILY_BYTES)}.`);
  }
  const reports = await read(db, `reports:${yesterday}`);
  if (reports.count >= limits.REPORTS_DAILY) {
    lines.push(`The day's limit of reports was reached on ${yesterday}: ${reports.count} of ${limits.REPORTS_DAILY}.`);
  }
  const notify = await read(db, `notify:${yesterday}`);
  if (notify.count >= limits.NOTIFY_DAILY) {
    lines.push(`The day's limit of addresses given was reached on ${yesterday}: `
      + `${notify.count} of ${limits.NOTIFY_DAILY}.`);
  }
  const { results } = await db.prepare("SELECT day, bytes FROM store_days WHERE day IN (?, ?)")
    .bind(today, yesterday).all();
  const held = Object.fromEntries(results.map((row) => [row.day, row.bytes]));
  const warn = STORE_WARN * limits.TOTAL_BYTES;
  if ((held[today] ?? 0) >= warn && !((held[yesterday] ?? 0) >= warn)) {
    lines.push(`The store of readouts holds ${megabytes(held[today])} of ${megabytes(limits.TOTAL_BYTES)}, `
      + `past ${Math.round(STORE_WARN * 100)}% (.github/readouts.md says how to make room).`);
  }
  return lines;
}

// Sends the lines, if any: what was done, for the nightly job's answer.
export async function alert(env, today, limits) {
  const lines = await alertLines(env.DB, today, limits);
  if (!lines.length) {
    return { sent: false, lines };
  }
  if (!env.EMAIL || !env.ALERTS_RECIPIENT_EMAIL) {
    return { sent: false, lines, why: "no EMAIL binding or ALERTS_RECIPIENT_EMAIL secret" };
  }
  try {
    await env.EMAIL.send({
      to: env.ALERTS_RECIPIENT_EMAIL,
      from: limits.from,
      subject: `Firmware Liberator's API: ${lines.length === 1 ? "one thing" : `${lines.length} things`} to look at`,
      text: [...lines, "", "The admin view has the detail. This was sent by the API's nightly job."].join("\n"),
    });
  } catch (error) {
    return { sent: false, lines, why: `the email could not be sent: ${error}` };
  }
  return { sent: true, lines };
}
