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
-- deleted takes its links with it; a link pending review is deleted by the
-- nightly job once its readout is offered.js PENDING_LINK_DAYS old, and never
-- kept while PENDING_LINKS_MOST others are (offered.js linkPruning).
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
-- as right, once a day (`day`, the last try), until offered.js
-- OFFER_RETRY_DAYS after its first (`since`). Names no readout or sender;
-- a file's rows are deleted once it is held, and by the nightly job once no
-- offer_links row gives the link or `day` is offered.js FAILURE_DAYS old.
CREATE TABLE IF NOT EXISTS offer_fetch_failures (
  sha TEXT NOT NULL,
  cloud TEXT NOT NULL,
  path TEXT NOT NULL,
  why TEXT NOT NULL CHECK (why IN ('other', 'none')),
  day TEXT NOT NULL,
  since TEXT NOT NULL,
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
-- Deleted by the nightly job once no offer_links row gives the link.
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

-- rejected_links: each link to review the admin view rejected (offered.js
-- rejectLink), by its file, its cloud and the link as sent (offered.js
-- sentAs), and the day: kept apart from the readouts that sent it, so a
-- rejected link sent again after they are deleted is still taken as rejected,
-- never put back for review. The newest offered.js REJECTED_LINKS_MOST are
-- kept (linkPruning).
CREATE TABLE IF NOT EXISTS rejected_links (
  sha TEXT NOT NULL,
  cloud TEXT NOT NULL,
  link TEXT NOT NULL,
  day TEXT NOT NULL,
  PRIMARY KEY (sha, cloud, link)
) WITHOUT ROWID;

-- report_offers: what a run's report says of the offers sent beside it (its
-- "offers", tools/autoflash/offers.py report_counts), one row a report:
-- whether they load eWeLink or come before the run's sign-in ended, and
-- whether offers come in from people's runs (the admin view). Counts and flags
-- alone; none for a report from a tool that sent none. `ran`: the sender
-- began; `why_not`: why none went (offers.py NOT_SENT), NULL where some did
-- or it was still going; `done`: it had ended as the report was made, its
-- counts otherwise those so far. `session_ended`: eWeLink ended the sign-in
-- as it asked; `run_session_ended`: the run's own calls found the sign-in
-- ended after it first asked, NULL where it asked nothing.
-- Deleted with its report.
CREATE TABLE IF NOT EXISTS report_offers (
  id TEXT PRIMARY KEY,
  ran INTEGER NOT NULL,
  why_not TEXT,
  done INTEGER NOT NULL,
  due INTEGER NOT NULL,
  requests INTEGER NOT NULL,
  sent INTEGER NOT NULL,
  taken INTEGER NOT NULL,
  have INTEGER NOT NULL,
  full INTEGER NOT NULL,
  refused INTEGER NOT NULL,
  review INTEGER NOT NULL,
  unfit INTEGER NOT NULL,
  session_ended INTEGER NOT NULL,
  run_session_ended INTEGER
) WITHOUT ROWID;
