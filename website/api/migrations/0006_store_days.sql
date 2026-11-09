-- The store of readouts' size by day (nightly.js). How migrations are
-- written and applied: ../database.js.

-- The readouts kept, in all, as each night found them: the admin view's
-- graph of how fast the store fills against its limit (index.js's
-- TOTAL_BYTES). Kept for good: a row a day.
CREATE TABLE IF NOT EXISTS store_days (
  day TEXT PRIMARY KEY,
  readouts INTEGER NOT NULL,
  bytes INTEGER NOT NULL
) WITHOUT ROWID;
