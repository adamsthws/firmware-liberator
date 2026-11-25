-- The identity check a run or readout sends (its report's `identity_check`,
-- tools/espressif/identity_check.py): whether the device's eWeLink identity,
-- in the flash it read, could be rebuilt from its account record, worked out
-- on the person's computer and sent as outcomes alone, never a value.
--
-- - `identity_check_platform`, of a run's report and a readout's: where the
--   device's firmware keeps its identity ('esf', 'ais', 'neither'); NULL
--   where the report gave no check, or came from a tool that sends none.
-- - identity_checks: each field's outcome, one row a field of a report that
--   gave one ('yes', 'no', 'missing', 'unknown'), its report's own id, in
--   reports or readout_reports. The fields are the tool's
--   (report-schema.json), and differ by platform.
ALTER TABLE reports ADD COLUMN identity_check_platform TEXT;
ALTER TABLE readout_reports ADD COLUMN identity_check_platform TEXT;

CREATE TABLE IF NOT EXISTS identity_checks (
  id TEXT NOT NULL,
  field TEXT NOT NULL,
  outcome TEXT NOT NULL,
  PRIMARY KEY (id, field)
) WITHOUT ROWID;
