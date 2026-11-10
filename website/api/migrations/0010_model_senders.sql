-- Two tables for the public figures' model names (snapshot.js), so a name
-- is shown only once several senders have given it over several days:
-- - model_senders: who named a model, as a keyed hash of the sender's
--   address and that model (reports.js), so it ties a sender to nothing
--   else, the network it came from the same way (null where unknown), and
--   the day. Deleted once the model is vouched for, or after VOUCH_KEEP_DAYS
--   (nightly.js).
-- - vouched_models: each model that had VOUCH_SENDERS senders, from
--   VOUCH_NETWORKS networks, over VOUCH_DAYS days, and the day it did, kept
--   for good.
--
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS model_senders (
  model TEXT NOT NULL,
  sender TEXT NOT NULL,
  network TEXT,
  day TEXT NOT NULL,
  PRIMARY KEY (model, sender, day)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS vouched_models (
  model TEXT PRIMARY KEY,
  day TEXT NOT NULL
) WITHOUT ROWID;
