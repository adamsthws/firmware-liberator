-- What migration 0003 kept in four tables of their own, one row a report
-- that gave one, as columns of the report itself (api/database.js: one value
-- a row is a column): each NULL where the report gave none, or came from a
-- tool that sends no such field.
--
-- reports (a run's):
-- - `question`: the question the person stopped it at, as a word
--   (tools/autoflash/report_data.py QUESTION_WORDS), never its words.
-- - `stock_check`: what the tool found when a firmware Sonoff's firmware
--   sent had not answered, before it asked for the device to be switched off
--   and on (report_data.stock_check): 'sonoff-answers' (not installed, sent
--   again), 'neither-answers' (most likely installed, not joining), or
--   'cannot-tell'.
-- - `table_refused_sha256`, `table_refused_at`: the partition table the run
--   refused (report_data.table_refused), its SHA-256 as the tool read it and
--   where: an Espressif device's flash address ('0x8000', '0xc000'), or
--   'update-file' for a Beken kind's official update file. A table is the
--   same on every device of a model and firmware: nothing of the device. The
--   nightly job emails the maintainer of a table first refused yesterday on
--   a kind the tool converts (alerts.js).
ALTER TABLE reports ADD COLUMN question TEXT;
ALTER TABLE reports ADD COLUMN stock_check TEXT;
ALTER TABLE reports ADD COLUMN table_refused_sha256 TEXT;
ALTER TABLE reports ADD COLUMN table_refused_at TEXT;

-- readout_reports (a readout's):
-- - `step`: the stage one that was stopped, failed or crashed was in
--   (tools/autoflash/readout.py STAGES), as a run's is its reports.step.
-- - `question`, `stock_check`: as a run's.
ALTER TABLE readout_reports ADD COLUMN step TEXT;
ALTER TABLE readout_reports ADD COLUMN question TEXT;
ALTER TABLE readout_reports ADD COLUMN stock_check TEXT;

-- What the four tables held, into those columns.
UPDATE reports SET question = (SELECT question FROM stop_questions WHERE id = reports.id)
  WHERE id IN (SELECT id FROM stop_questions);
UPDATE reports SET stock_check = (SELECT outcome FROM stock_checks WHERE id = reports.id)
  WHERE id IN (SELECT id FROM stock_checks);
UPDATE reports SET (table_refused_sha256, table_refused_at) =
    (SELECT sha256, at FROM refused_tables WHERE id = reports.id)
  WHERE id IN (SELECT id FROM refused_tables);
UPDATE readout_reports SET step = (SELECT step FROM readout_steps WHERE id = readout_reports.id)
  WHERE id IN (SELECT id FROM readout_steps);
UPDATE readout_reports SET question = (SELECT question FROM stop_questions WHERE id = readout_reports.id)
  WHERE id IN (SELECT id FROM stop_questions);
UPDATE readout_reports SET stock_check = (SELECT outcome FROM stock_checks WHERE id = readout_reports.id)
  WHERE id IN (SELECT id FROM stock_checks);

DROP TABLE stop_questions;
DROP TABLE stock_checks;
DROP TABLE refused_tables;
DROP TABLE readout_steps;
