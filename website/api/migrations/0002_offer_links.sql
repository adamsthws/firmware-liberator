-- The links a first stage, or a whole readout, sends in place of its offered
-- files (index.js offers and readout, offered.js), so the API fetches each
-- file itself.

-- offer_links: each file's link a readout gave, by the file's SHA-256: the
-- path on its cloud's file server, and the cloud (offered.js CLOUDS), which
-- says where that path is fetched from; and `bytes`, the file's size once
-- this link fetched it and it is held under this readout (NULL till then).
-- Deleted with its readout, as readout_firmware's marks are, so a readout
-- deleted takes its links with it.
CREATE TABLE IF NOT EXISTS offer_links (
  readout TEXT NOT NULL,
  sha TEXT NOT NULL,
  path TEXT NOT NULL,
  cloud TEXT NOT NULL,
  bytes INTEGER,
  PRIMARY KEY (readout, sha)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS offer_links_sha ON offer_links (sha);

-- offer_fetch_failures: each link that did not give its file (offered.js),
-- so it is not fetched again for that SHA-256 on first sight: `why` 'other'
-- where it gave another file, or one too large, and is never tried again
-- for it; 'none' where it gave nothing, tried again only for an offer taken
-- as right, once a day (`day`, the last try). Names no readout or sender;
-- a file's rows are deleted once it is held.
CREATE TABLE IF NOT EXISTS offer_fetch_failures (
  sha TEXT NOT NULL,
  cloud TEXT NOT NULL,
  path TEXT NOT NULL,
  why TEXT NOT NULL CHECK (why IN ('other', 'none')),
  day TEXT NOT NULL,
  PRIMARY KEY (sha, cloud, path)
) WITHOUT ROWID;
