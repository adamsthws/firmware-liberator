-- The reports the tool sends at the end of a run (reports.js), and what is
-- kept of them for good; the nightly job's totals by day, model and the rest
-- come in a migration of their own. How migrations are written and applied:
-- ../database.js.

-- One a report, as report-schema.json describes it, for a year (the nightly
-- job deletes older ones once their totals are kept). `day` is when it came
-- in, as a UTC date: never the time, nor anything of the sender. `device_hash`
-- is the keyed hash of the tool's hash of the device's MAC. `quarantined`: left
-- out of every figure, set by hand for reports found to be made up.
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  kind TEXT NOT NULL,
  result TEXT NOT NULL,
  step TEXT,
  error TEXT,
  text TEXT,
  family TEXT,
  device TEXT,
  model TEXT,
  stock TEXT,
  device_hash TEXT,
  firmware TEXT,
  source TEXT NOT NULL,
  backup TEXT NOT NULL,
  identity TEXT,
  os_family TEXT NOT NULL,
  os_version TEXT NOT NULL,
  python_version TEXT NOT NULL,
  python_source TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  resumed INTEGER NOT NULL,
  stages TEXT NOT NULL,
  signal INTEGER,
  wifi_typed INTEGER,
  setup_network INTEGER,
  power_cycled INTEGER,
  checks TEXT NOT NULL,
  meter TEXT,
  quarantined INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS reports_day ON reports (day);
CREATE INDEX IF NOT EXISTS reports_device_day ON reports (device_hash, day) WHERE device_hash IS NOT NULL;

-- One a device, kept for good, so a second run on it is known as the same
-- device: when it was first and last seen, how its first run ended, how many
-- runs it took to its first conversion, and how many conversions and returns
-- to Sonoff's firmware it has had. `dev`: seen in a report from the
-- maintainer's own testing, so left out of the public figures.
CREATE TABLE IF NOT EXISTS devices (
  device_hash TEXT PRIMARY KEY,
  model TEXT,
  device TEXT,
  first_day TEXT NOT NULL,
  last_day TEXT NOT NULL,
  first_result TEXT NOT NULL,
  converted_day TEXT,
  runs INTEGER NOT NULL,
  runs_to_convert INTEGER,
  conversions INTEGER NOT NULL,
  back_to_sonoff INTEGER NOT NULL,
  firmware TEXT,
  stock TEXT,
  dev INTEGER NOT NULL
) WITHOUT ROWID;

-- How many times a day /wanted was asked of each product model: the
-- devices people would read out for the tool to learn. Kept for good.
CREATE TABLE IF NOT EXISTS wanted_days (
  day TEXT NOT NULL,
  model TEXT NOT NULL,
  asked INTEGER NOT NULL,
  PRIMARY KEY (day, model)
) WITHOUT ROWID;
