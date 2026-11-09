-- Each readout kept (index.js), so that each product, firmware model and
-- version (`key`) is kept from READOUTS_PER_KEY different senders at most,
-- and reviewed until that many agree.
-- - `sender` is a keyed hash (SENDER_KEY) of the sender's address and the
--   key, never the address: one sender's readouts of one key are one, and
--   nothing ties a sender's readouts of different keys together.
-- - `fingerprint` is a hash of what every genuine readout of the key shares
--   (its firmware, not the owner's settings): readouts that agree share it.
-- - `review`: pending until READOUTS_PER_KEY of one stage agree (quorum), or
--   the maintainer approves or rejects it; a rejected one frees its place.
--
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS readouts (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL,
  stage TEXT NOT NULL CHECK (stage IN ('first', 'whole')),
  sender TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  object TEXT NOT NULL,
  received TEXT NOT NULL,
  review TEXT NOT NULL DEFAULT 'pending' CHECK (review IN ('pending', 'quorum', 'approved', 'rejected')),
  UNIQUE (key, sender)
);

CREATE INDEX IF NOT EXISTS readouts_key ON readouts (key, stage);
CREATE INDEX IF NOT EXISTS readouts_review ON readouts (review);
