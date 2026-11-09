// The counts the API's limits are held to, in the database's counters table
// (migrations/0001_counters.sql): one row a name, each changed in a single
// statement, so two requests at once can never both pass a limit.

// One more of `name`, adding `bytes`, if that keeps it within `most` and
// `mostBytes`: whether it did. `day` (YYYY-MM-DD) marks a day's count, which
// the nightly job deletes later; null keeps it for good.
export async function reserve(db, name, day, { bytes = 0, most = Number.MAX_SAFE_INTEGER,
  mostBytes = Number.MAX_SAFE_INTEGER } = {}) {
  if (bytes > mostBytes || most < 1) {
    return false;
  }
  const row = await db.prepare(
    `INSERT INTO counters (name, day, count, bytes) VALUES (?1, ?2, 1, ?3)
     ON CONFLICT (name) DO UPDATE SET count = count + 1, bytes = bytes + excluded.bytes
     WHERE count < ?4 AND bytes + excluded.bytes <= ?5
     RETURNING count`).bind(name, day, bytes, most, mostBytes).first();
  return row !== null;
}

// Gives back one reserved, and its bytes: for one reserved and then not kept.
export async function release(db, name, bytes = 0) {
  await db.prepare(
    "UPDATE counters SET count = max(count - 1, 0), bytes = max(bytes - ?2, 0) WHERE name = ?1",
  ).bind(name, bytes).run();
}

// {count, bytes} of `name`: zeroes for one never counted.
export async function read(db, name) {
  const row = await db.prepare("SELECT count, bytes FROM counters WHERE name = ?").bind(name).first();
  return { count: row?.count ?? 0, bytes: row?.bytes ?? 0 };
}
