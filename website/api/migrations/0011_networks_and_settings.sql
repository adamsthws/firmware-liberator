-- Two tables for index.js, and one for the admin view:
-- - readout_networks: the network (Cloudflare's ASN for the request) each
--   readout came from, as a keyed hash of it and the readout's key, so it ties
--   readouts of different keys together no more than their senders do.
--   Readouts agree only once they come from MIN_NETWORKS networks, and a
--   key's places past PER_KEY are kept for a network none of them came from.
--   Deleted with its readout.
-- - settings: the maintainer's switches, by name, set in the admin view:
--   email_on_agreement, "on" or "off" (on when not set).
--
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS readout_networks (
  id TEXT PRIMARY KEY,
  network TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS settings (
  name TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;
