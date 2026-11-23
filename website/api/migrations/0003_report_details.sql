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
