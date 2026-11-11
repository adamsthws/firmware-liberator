-- What a report says beyond the columns of `reports`, as the JSON object it
-- came in (reports.js EXTRAS): where in the tool's code a run went wrong, the
-- chip and flash, what on the computer's side may get in the way, eWeLink's
-- region, how the tool was started, and the like. Deleted with its report.
-- How migrations are written and applied: ../database.js.
CREATE TABLE IF NOT EXISTS report_extras (
  id TEXT PRIMARY KEY,
  extras TEXT NOT NULL
) WITHOUT ROWID;
