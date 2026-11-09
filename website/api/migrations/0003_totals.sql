-- What the nightly job (nightly.js) keeps of the reports for good: their
-- totals by day, and by month the figures a total cannot give (run times,
-- step times, what people run, the Sonoff firmware met). Each row's `tag`
-- is the report's ("user" or "dev", the maintainer's own testing), so the
-- public figures can leave the testing out. A value a report did not have
-- is '' here, as each of these columns is part of its table's key.
-- How migrations are written and applied: ../database.js.

-- The reports of each day, counted by what they say: the public figures'
-- runs, results, routes, failures and tool versions come from here.
CREATE TABLE IF NOT EXISTS daily_totals (
  day TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  device TEXT NOT NULL,
  model TEXT NOT NULL,
  firmware TEXT NOT NULL,
  source TEXT NOT NULL,
  result TEXT NOT NULL,
  step TEXT NOT NULL,
  error TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (day, tag, tool, device, model, firmware, source, result, step, error)
) WITHOUT ROWID;

-- daily_totals, by month: what the public snapshot reads, so a night's
-- reading grows with the months kept, not the days. The nightly job makes
-- the current and the previous month's again from daily_totals each night.
CREATE TABLE IF NOT EXISTS monthly_totals (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  device TEXT NOT NULL,
  model TEXT NOT NULL,
  firmware TEXT NOT NULL,
  source TEXT NOT NULL,
  result TEXT NOT NULL,
  step TEXT NOT NULL,
  error TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (month, tag, tool, device, model, firmware, source, result, step, error)
) WITHOUT ROWID;

-- The days whose reports daily_totals counts: each folded once. Removing a
-- day here, and its totals, has the next night count it again, as after a
-- report of it was quarantined.
CREATE TABLE IF NOT EXISTS folded (
  day TEXT PRIMARY KEY
) WITHOUT ROWID;

-- How long a conversion took, in seconds, by device and route: the median
-- and the 90th percentile.
CREATE TABLE IF NOT EXISTS monthly_times (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  device TEXT NOT NULL,
  firmware TEXT NOT NULL,
  source TEXT NOT NULL,
  n INTEGER NOT NULL,
  p50 INTEGER NOT NULL,
  p90 INTEGER NOT NULL,
  PRIMARY KEY (month, tag, device, firmware, source)
) WITHOUT ROWID;

-- Each step's time, by step ID and tool version: a step that changes is a
-- new version's, so never joins another's figures.
CREATE TABLE IF NOT EXISTS monthly_steps (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  step TEXT NOT NULL,
  n INTEGER NOT NULL,
  p50 INTEGER NOT NULL,
  p90 INTEGER NOT NULL,
  PRIMARY KEY (month, tag, tool, step)
) WITHOUT ROWID;

-- The computers runs were made on: where support effort goes.
CREATE TABLE IF NOT EXISTS monthly_environment (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  os_family TEXT NOT NULL,
  python_source TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (month, tag, os_family, python_source)
) WITHOUT ROWID;

-- The Sonoff firmware versions met, by model, and how many of those runs
-- converted: an early sign of an update that closes a way in.
CREATE TABLE IF NOT EXISTS monthly_stock (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  model TEXT NOT NULL,
  stock TEXT NOT NULL,
  runs INTEGER NOT NULL,
  converted INTEGER NOT NULL,
  PRIMARY KEY (month, tag, model, stock)
) WITHOUT ROWID;
