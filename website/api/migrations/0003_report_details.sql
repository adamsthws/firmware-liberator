-- The partition table a run refused (a run's report's "table_refused",
-- tools/autoflash/report_data.py table_refused), one row a report that
-- gives one: none from a run that refused none, nor from a tool that sends
-- no such field. `sha256`, the table's SHA-256 as the tool read it; `at`,
-- where: an Espressif device's flash address ('0x8000', '0xc000'), or
-- 'update-file' for a Beken kind's official update file. A table is the same
-- on every device of a model and firmware: nothing of the device. The
-- nightly job emails the maintainer of a table first refused yesterday on a
-- kind the tool converts (alerts.js).
CREATE TABLE IF NOT EXISTS refused_tables (
  id TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL,
  at TEXT NOT NULL
) WITHOUT ROWID;

-- Where a run or a readout stopped, beyond what reports and readout_reports
-- hold (a report's stopped "question", and a readout's stopped "step",
-- tools/autoflash/report_data.py), one row a report that gives one: none
-- from a report that ended well, stopped at no question, or came from a tool
-- that sends no such field. New tables, not new columns, so this runs again
-- harmlessly and the Worker deployed before it never sees them.
--
-- `question`: the question the person stopped a run or readout at, as a
-- word (report_data.QUESTION_WORDS), never its words. Its report's own id,
-- in reports or readout_reports.
CREATE TABLE IF NOT EXISTS stop_questions (
  id TEXT PRIMARY KEY,
  question TEXT NOT NULL
) WITHOUT ROWID;

-- `step`: the stage a readout that was stopped, failed or crashed was in
-- (tools/autoflash/readout.py STAGES), as a run's is its reports.step.
CREATE TABLE IF NOT EXISTS readout_steps (
  id TEXT PRIMARY KEY,
  step TEXT NOT NULL
) WITHOUT ROWID;

-- `outcome`: what the tool found when a firmware Sonoff's firmware sent had
-- not answered, before it asked for the device to be switched off and on (a
-- run's or readout's report's "stock_check", report_data.stock_check):
-- 'sonoff-answers' (not installed, sent again), 'neither-answers' (most
-- likely installed, not joining), or 'cannot-tell'. One row a report that
-- gives one: none where no check ran, nor from a tool that sends no such
-- field. Its report's own id, in reports or readout_reports.
CREATE TABLE IF NOT EXISTS stock_checks (
  id TEXT PRIMARY KEY,
  outcome TEXT NOT NULL
) WITHOUT ROWID;
