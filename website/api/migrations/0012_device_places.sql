-- Each device's number among those converted on the computer that first
-- converted it (a report's nth_device, from the tool's own records): how many
-- devices one computer converts, with nothing that names the computer. Kept
-- as a device's record is, written with it, and never changed once written.
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS device_places (
  device_hash TEXT PRIMARY KEY,
  place INTEGER NOT NULL
) WITHOUT ROWID;
