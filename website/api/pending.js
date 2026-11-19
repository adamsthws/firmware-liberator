// What the API keeps only for a while (index.js), and the nightly job's
// clearing of it (prunePending, called from nightly.js):
// - Readouts not taken as right - pending, with no three agreeing and no
//   approval, or rejected - are deleted PENDING_DAYS after they came in, with
//   the marks of the offered files they held and the links they gave
//   (offered.js), giving back their bytes and places, so made-up readouts
//   cannot fill the store or a key's places; a file fetched that a readout
//   taken as right links too is kept, its mark moved to it. But
//   for one disputing readouts taken as right (checked, index.js CHECK_DAYS):
//   kept until the maintainer settles it, so a dispute never goes unseen. A
//   key holds MAX_PLACES at most, so these cannot fill the store either.
// - Addresses given to /v1/notify and never confirmed are deleted
//   SIGNUP_DAYS after they were given.
import { release } from "./counters.js";

// Half a year: a rare device's owners may come months apart, and a readout
// deleted before the next arrives never meets one to agree with.
export const PENDING_DAYS = 180;
export const SIGNUP_DAYS = 7;
// The count of bytes held by readouts not yet taken as right (counters.js).
export const PENDING = "readouts:pending";
// The name index.js counts every readout's bytes under (READOUTS_EVER).
export const READOUTS_EVER = "readouts:all";
// Readouts deleted a night at most: more than a day's intake, DAILY_COUNT
// readouts and OFFERS_DAILY offers (index.js), so a backlog clears within
// nights; in a fixed few queries whatever the number, and R2 deletes of
// R2_DELETE_MOST keys each, the most one call takes.
export const PRUNE_EACH_NIGHT = 2500;
export const R2_DELETE_MOST = 1000;

const DAY = 24 * 60 * 60 * 1000;
const dayBefore = (today, days) => new Date(Date.parse(`${today}T00:00:00Z`) - days * DAY).toISOString();

// The readouts ?1 (a JSON list of their ids) names, for a statement.
const AMONG = "SELECT value FROM json_each(?1)";

// Clears what has been kept too long, as of `today` (YYYY-MM-DD): how many
// readouts and sign-ups it deleted. A file a readout deleted had fetched by
// its link (offered.js) is not deleted when a readout taken as right links
// it too: its mark moves to that readout (the file's own metadata still
// naming the one that fetched it), so a made-up readout naming a real file
// cannot take it from the offer that agreed on it.
export async function prunePending(env, today) {
  const old = (await env.DB.prepare(
    `SELECT id, object, bytes, review FROM readouts WHERE review IN ('pending', 'rejected') AND received < ?1
     AND NOT (review = 'pending' AND checked = 1) ORDER BY received LIMIT ?2`,
  ).bind(dayBefore(today, PENDING_DAYS), PRUNE_EACH_NIGHT).all()).results;
  const ids = JSON.stringify(old.map((row) => row.id));
  // Each mark of theirs, with where it moves: a readout taken as right
  // linking a file fetched by one of these.
  const marks = old.length ? (await env.DB.prepare(`SELECT readout_firmware.readout, readout_firmware.sha,
      offer_links.bytes, (SELECT min(keeper.readout) FROM offer_links AS keeper
        JOIN readouts ON readouts.id = keeper.readout
        WHERE keeper.sha = readout_firmware.sha AND readouts.review IN ('quorum', 'approved')) AS keeper,
      EXISTS (SELECT 1 FROM readout_firmware AS other WHERE other.sha = readout_firmware.sha
        AND other.readout NOT IN (${AMONG})) AS marked
    FROM readout_firmware LEFT JOIN offer_links ON offer_links.readout = readout_firmware.readout
      AND offer_links.sha = readout_firmware.sha
    WHERE readout_firmware.readout IN (${AMONG})`).bind(ids).all()).results : [];
  const moving = marks.filter((mark) => mark.bytes !== null && mark.keeper !== null);
  const kept = new Set(moving.map((mark) => mark.sha));
  const keys = [...old.map((row) => row.object),
    // A file another readout kept marks too, as two sent together may, stays.
    ...marks.filter((mark) => !kept.has(mark.sha) && !mark.marked).map((mark) => `readouts/firmware/${mark.sha}`)];
  const moved = (id) => moving.filter((mark) => mark.readout === id).reduce((sum, mark) => sum + mark.bytes, 0);
  if (old.length) {
    await env.DB.batch([
      ...moving.flatMap((mark) => [
        // Its own mark alone, others' of the file left as they are; one the
        // keeper has already stays, and this one goes with the rest below.
        env.DB.prepare("UPDATE OR IGNORE readout_firmware SET readout = ?3 WHERE readout = ?1 AND sha = ?2")
          .bind(mark.readout, mark.sha, mark.keeper),
        env.DB.prepare("UPDATE readouts SET bytes = bytes + ?2 WHERE id = ?1").bind(mark.keeper, mark.bytes),
        env.DB.prepare("UPDATE offer_links SET bytes = ?3 WHERE readout = ?1 AND sha = ?2")
          .bind(mark.keeper, mark.sha, mark.bytes),
      ]),
      ...["readout_firmware WHERE readout", "offer_links WHERE readout", "readout_networks WHERE id", "readouts WHERE id"]
        .map((which) => env.DB.prepare(`DELETE FROM ${which} IN (${AMONG})`).bind(ids)),
    ]);
  }
  // Only once no row names them: a night whose batch fails deletes nothing,
  // and the next tries the same again.
  for (let at = 0; at < keys.length; at += R2_DELETE_MOST) {
    await env.READOUTS.delete(keys.slice(at, at + R2_DELETE_MOST));
  }
  // Their bytes given back, but those of the files moved, which the store
  // still holds; all of a pending one's, as a file moved is now its keeper's,
  // taken as right.
  const bytes = old.reduce((sum, row) => sum + row.bytes - moved(row.id), 0);
  const waiting = old.filter((row) => row.review === "pending");
  if (old.length) {
    await release(env.DB, READOUTS_EVER, bytes, old.length);
  }
  if (waiting.length) {
    await release(env.DB, PENDING, waiting.reduce((sum, row) => sum + row.bytes, 0), waiting.length);
  }
  const signups = await env.DB.prepare("DELETE FROM confirmations WHERE day < ?1").bind(
    dayBefore(today, SIGNUP_DAYS).slice(0, 10)).run();
  return { readouts: old.length, signups: signups.meta?.changes ?? 0 };
}
