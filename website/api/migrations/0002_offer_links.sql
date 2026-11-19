-- The links a first stage, or a whole readout, sends in place of its offered
-- files (index.js offers and readout, offered.js), so the API fetches each
-- file itself.

-- offer_links: each file's link a readout gave, by the file's SHA-256: the
-- path on its cloud's file server, and the cloud (offered.js CLOUDS), which
-- says where that path is fetched from; and `bytes`, the file's size once
-- this link fetched it and it is held under this readout (NULL till then).
-- A link of a shape not seen before (a `url`, offered.js linkParts) keeps
-- its `scheme`, `host` and `port` (NULL for none) beside its path, all NULL
-- for a link of the cloud's own shape. `review`: 'auto' for a link of the
-- cloud's shape, fetched unasked; 'pending' for a `url`, never fetched until
-- the admin view approves it ('approved') or rejects it ('rejected').
-- Deleted with its readout, as readout_firmware's marks are, so a readout
-- deleted takes its links with it.
CREATE TABLE IF NOT EXISTS offer_links (
  readout TEXT NOT NULL,
  sha TEXT NOT NULL,
  path TEXT NOT NULL,
  cloud TEXT NOT NULL,
  bytes INTEGER,
  scheme TEXT CHECK (scheme IN ('http', 'https')),
  host TEXT,
  port INTEGER,
  review TEXT NOT NULL DEFAULT 'auto' CHECK (review IN ('auto', 'pending', 'approved', 'rejected')),
  PRIMARY KEY (readout, sha)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS offer_links_sha ON offer_links (sha);
CREATE INDEX IF NOT EXISTS offer_links_review ON offer_links (review);

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

-- link_peeks: the first bytes of each link to review (offered.js peekNamed),
-- asked for once when the link is first taken and never again, so the
-- maintainer sees what it gives before approving it. By the link as sent
-- (`link`, offered.js sentAs) of its cloud. `outcome`: 'answered', a
-- response read; 'redirect', a redirect, recorded (`location`, as given) and
-- not followed; 'timeout'; or 'failed', no response. `status` the HTTP
-- status, `size` the whole file's size where Content-Range gave it, `server`
-- the host that answered, `kind` what its bytes start like (offered.js
-- PEEK_KINDS), `names` its wrapper's firmware names joined by "+", `hex` the
-- bytes read, at most offered.js PEEK_BYTES. Nothing else of the body is kept.
CREATE TABLE IF NOT EXISTS link_peeks (
  cloud TEXT NOT NULL,
  link TEXT NOT NULL,
  day TEXT NOT NULL,
  outcome TEXT NOT NULL DEFAULT 'failed' CHECK (outcome IN ('answered', 'redirect', 'timeout', 'failed')),
  status INTEGER,
  size INTEGER,
  server TEXT,
  location TEXT,
  kind TEXT,
  names TEXT,
  hex TEXT,
  PRIMARY KEY (cloud, link)
) WITHOUT ROWID;
