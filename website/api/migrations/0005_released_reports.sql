-- `released`: a report the maintainer released from review (admin/index.js
-- releasing). Its day is folded again the next night, and the nightly job's
-- spike check (nightly.js spikes) neither counts nor holds one, or a real
-- rush released would be held again every night.
ALTER TABLE reports ADD COLUMN released INTEGER NOT NULL DEFAULT 0;
