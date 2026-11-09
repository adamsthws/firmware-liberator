-- Two things for index.js and pending.js:
-- - signups: an address given to /notify, kept only until its owner
--   confirms it through the link emailed to it (/confirm), or SIGNUP_DAYS
--   pass, when the nightly job deletes it: an address nobody confirms is
--   never written to again, nor kept.
-- - readout_firmware: each offered file a readout holds, marked under
--   readouts/firmware/, so a readout deleted takes its marks with it and the
--   next readout of the device sends the file again.
--
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS signups (
  token TEXT PRIMARY KEY,
  who TEXT NOT NULL,
  email TEXT NOT NULL,
  devices TEXT NOT NULL,
  day TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS signups_day ON signups (day);

CREATE TABLE IF NOT EXISTS readout_firmware (
  readout TEXT NOT NULL,
  sha TEXT NOT NULL,
  PRIMARY KEY (readout, sha)
) WITHOUT ROWID;
