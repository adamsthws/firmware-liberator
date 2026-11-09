-- Reports held back for the maintainer's review (review.js). How migrations
-- are written and applied: ../database.js.

-- Each report held, and why: it is quarantined in `reports` too, so in no
-- figure until released in the admin view (which also unfolds its day).
CREATE TABLE IF NOT EXISTS held_reports (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  why TEXT NOT NULL
) WITHOUT ROWID;
