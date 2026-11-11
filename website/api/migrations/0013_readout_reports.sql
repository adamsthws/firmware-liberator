-- The report of each device's readout (reports.js, a report with "kind":
-- "readout"), apart from the runs' reports: how readouts end, by device,
-- and how many devices the computers that make them have converted. As a
-- run's report is kept: `day` is when it came in, as a UTC date, never the
-- time nor anything of the sender; `device_hash` the keyed hash of the tool's
-- hash of the device's MAC; `quarantined`, one held for review. Deleted after
-- a year (nightly.js). How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS readout_reports (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  result TEXT NOT NULL,
  error TEXT,
  family TEXT,
  device TEXT,
  model TEXT,
  stock_model TEXT,
  stock TEXT,
  whole INTEGER,
  device_hash TEXT,
  os_family TEXT NOT NULL,
  os_version TEXT NOT NULL,
  python_version TEXT NOT NULL,
  python_source TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  converted_here INTEGER NOT NULL,
  quarantined INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS readout_reports_day ON readout_reports (day);
CREATE INDEX IF NOT EXISTS readout_reports_device_day ON readout_reports (device_hash, day)
  WHERE device_hash IS NOT NULL;
