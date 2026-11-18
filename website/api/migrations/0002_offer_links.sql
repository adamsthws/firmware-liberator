-- The links a first stage sends in place of its offered files (index.js
-- offers, offered.js), so the API fetches a file itself once its offer is
-- taken as right.

-- offer_links: each file's link a readout gave, by the file's SHA-256: the
-- path on its cloud's file server, and the cloud (offered.js CLOUDS), which
-- says where that path is fetched from. Deleted with its readout, as
-- readout_firmware's marks are, so a readout deleted takes its links with it.
CREATE TABLE IF NOT EXISTS offer_links (
  readout TEXT NOT NULL,
  sha TEXT NOT NULL,
  path TEXT NOT NULL,
  cloud TEXT NOT NULL,
  PRIMARY KEY (readout, sha)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS offer_links_sha ON offer_links (sha);
