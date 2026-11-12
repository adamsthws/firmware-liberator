-- readout_firmware: each offered file a readout holds, marked under
-- readouts/firmware/ (index.js), so a readout deleted takes its marks with it
-- and the next readout of the device sends the file again.
--
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS readout_firmware (
  readout TEXT NOT NULL,
  sha TEXT NOT NULL,
  PRIMARY KEY (readout, sha)
) WITHOUT ROWID;
