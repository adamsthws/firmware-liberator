// The API's D1 database (DB in wrangler.jsonc). Made and brought up to date
// by tools/repo/cloudflare_settings.py apply, with the maintainer's token,
// never by CI, as D1 permissions reach every database in the account: it
// runs each file in migrations/ it has not run yet, in the order of their
// names, and records each in d1_migrations, as wrangler would.
// - A migration, once committed, is never changed: a change is a new file,
//   with the next number (0002_..., ...).
// - One value a row is a column of that row's table, NULL where it has
//   none, so a report's every field is read from the report's own row. A
//   table of its own is for many rows to one (report_stages), or a group of
//   values most rows have none of (report_offers).
// - A migration may change what is there: add a column, copy, drop a table.
//   Running one over the API is not atomic, and only one that makes tables
//   alone (CREATE ... IF NOT EXISTS) can be run again: one that changes any,
//   cut off part way, stops the next `apply` at what it had done already
//   (duplicate column name, no such table). Run the statements it had left
//   by hand, in the D1 console, then its row into d1_migrations.
// - `apply` comes before the push that deploys the Worker needing it. That
//   Worker answers 503 until the migration is there (ready, below); the one
//   deployed before may fail, between the two, on what a migration took away.

// The newest migration this code needs (test/database.test.mjs holds it to
// the newest file in migrations/).
export const LATEST_MIGRATION = "0005_released_reports.sql";

// Whether the database has every migration this code needs, asked at most
// once a minute in a Worker: false until `apply` has run the newest, so a
// Worker deployed first answers 503 rather than failing on a missing table.
const checked = new WeakMap();

export async function ready(db, now = Date.now()) {
  const last = checked.get(db);
  if (last && last.ready && now - last.at < 60_000) {
    return true;
  }
  let found = null;
  try {
    found = await db.prepare("SELECT name FROM d1_migrations WHERE name = ?").bind(LATEST_MIGRATION).first("name");
  } catch {
    found = null;   // no d1_migrations: never migrated
  }
  checked.set(db, { at: now, ready: found === LATEST_MIGRATION });
  return found === LATEST_MIGRATION;
}
