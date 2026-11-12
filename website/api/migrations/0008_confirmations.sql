-- The addresses given to be emailed as more devices are supported (index.js's
-- notify and subscribe), each under `who`, the SHA-256 of the address in
-- lower case:
-- - confirmations: one given, waiting for its owner to follow the link
--   emailed to it (token), with the devices (a JSON list of {product, model},
--   [] for none) and readouts (a JSON list of their references) it named, and
--   when the link was sent (sent), so remind() sends it once more
--   REMIND_AFTER_DAYS after. Deleted when confirmed, or by the nightly job
--   SIGNUP_DAYS after (pending.js).
-- - subscribers: one confirmed, once an address, with the token of the
--   removal link every email to it carries (/forget).
-- - subscriber_devices: every device its sign-ups named, one row a product
--   and firmware model, with when it was first and last named, so the owners
--   of one product are one query (tools/repo/readouts.py).
-- - subscriber_readouts: the readouts its sign-ups named, so a readout's
--   sender can be asked to try a build.
--
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS confirmations (
  token TEXT PRIMARY KEY,
  who TEXT NOT NULL,
  email TEXT NOT NULL,
  devices TEXT NOT NULL,
  readouts TEXT NOT NULL,
  day TEXT NOT NULL,
  sent TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS confirmations_sent ON confirmations (sent);

CREATE TABLE IF NOT EXISTS subscribers (
  who TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  token TEXT NOT NULL,
  since TEXT NOT NULL,
  updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriber_devices (
  who TEXT NOT NULL,
  product TEXT NOT NULL,
  model TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY (who, product, model)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS subscriber_devices_product ON subscriber_devices (product);

CREATE TABLE IF NOT EXISTS subscriber_readouts (
  who TEXT NOT NULL,
  readout TEXT NOT NULL,
  PRIMARY KEY (who, readout)
) WITHOUT ROWID;
