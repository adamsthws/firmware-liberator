-- The counts the API's limits are held to (counters.js): one row a name.
-- `day` is set for a day's count, which the nightly job deletes a week
-- later, and NULL for one kept for good, such as the bytes of every readout.
--
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS counters (
  name TEXT PRIMARY KEY,
  day TEXT,
  count INTEGER NOT NULL DEFAULT 0,
  bytes INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS counters_day ON counters (day);
