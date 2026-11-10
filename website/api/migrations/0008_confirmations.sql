-- confirmations: an address given to /notify waiting for its owner to follow
-- the link emailed to it (index.js), as 0007's signups held it, with the time
-- the link was sent (sent), so remind() sends it once more REMIND_AFTER_DAYS
-- after, to the hour, not on the day's date alone. Deleted when confirmed, or
-- by the nightly job SIGNUP_DAYS after (pending.js).
--
-- signups goes: it was never used by a Worker before this one, so it holds
-- nothing to carry over, and run again this drops nothing in use.
--
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS confirmations (
  token TEXT PRIMARY KEY,
  who TEXT NOT NULL,
  email TEXT NOT NULL,
  devices TEXT NOT NULL,
  day TEXT NOT NULL,
  sent TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS confirmations_sent ON confirmations (sent);

DROP TABLE IF EXISTS signups;
