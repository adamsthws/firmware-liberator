-- The emails the API sends (mail.js), by day and outcome, kept for good: the
-- nightly job (nightly.js) copies each day's counts here from `counters`,
-- which keeps them a week, for the admin view's history. `code` is Email
-- Service's error code for a failed send, '' for one sent. How migrations
-- are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS mail_days (
  day TEXT NOT NULL,
  outcome TEXT NOT NULL,
  code TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (day, outcome, code)
) WITHOUT ROWID;
