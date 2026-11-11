-- The reports the tool sends at the end of a run (reports.js), and what is
-- kept of them for good; the nightly job's totals by day, model and the rest
-- come in a migration of their own. How migrations are written and applied:
-- ../database.js.

-- One a report, as report-schema.json describes it, for a year (the nightly
-- job deletes older ones once their totals are kept). `day` is when it came
-- in, as a UTC date: never the time, nor anything of the sender. `device_hash`
-- is the keyed hash of the tool's hash of the device's MAC. `quarantined`: left
-- out of every figure, set by hand for reports found to be made up. Each other
-- column is the report's field of that name, a nested one's flattened
-- (`os_arch` is os.arch, `flash_mb` chip.flash_mb, `crash_site` stopped.where);
-- a true/false one is 1/0.
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
  os_arch TEXT NOT NULL,
  python_version TEXT NOT NULL,
  python_source TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  resumed INTEGER NOT NULL,
  signal INTEGER,
  wifi_typed INTEGER,
  setup_network INTEGER,
  power_cycled INTEGER,
  checks TEXT NOT NULL,
  meter TEXT,
  crash_site TEXT,
  chip_revision TEXT,
  flash_id TEXT,
  flash_mb INTEGER,
  connection TEXT,
  vpn INTEGER,
  networks INTEGER NOT NULL,
  security_note INTEGER NOT NULL,
  firewall TEXT,
  admin INTEGER NOT NULL,
  ewelink_region TEXT,
  ctrl_c_carried_on INTEGER NOT NULL,
  launcher TEXT NOT NULL,
  install TEXT NOT NULL,
  begun_on TEXT,
  back_after_power_cycle INTEGER,
  firmware_name TEXT,
  firmware_name_expected INTEGER,
  quarantined INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS reports_day ON reports (day);
CREATE INDEX IF NOT EXISTS reports_device_day ON reports (device_hash, day) WHERE device_hash IS NOT NULL;

-- A report's stages, one row each, as its `stages` gives them: the stage's
-- ID, its time and the device's busy time in seconds, its tries, and the
-- seconds the tool waited on the person. Deleted with its report.
CREATE TABLE IF NOT EXISTS report_stages (
  id TEXT NOT NULL,
  stage TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  busy INTEGER NOT NULL,
  tries INTEGER NOT NULL,
  wait INTEGER NOT NULL,
  PRIMARY KEY (id, stage)
) WITHOUT ROWID;

-- One a device, kept for good, so a second run on it is known as the same
-- device: when it was first and last seen, how its first run ended, how many
-- runs it took to its first conversion, and how many conversions and returns
-- to Sonoff's firmware it has had. `place`: its number among the devices
-- converted on the computer that first converted it (that report's
-- nth_device), never changed once given: how many devices one computer
-- converts, with nothing that names the computer. `dev`: seen in a report
-- from the maintainer's own testing, so left out of the public figures.
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
  place INTEGER,
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
