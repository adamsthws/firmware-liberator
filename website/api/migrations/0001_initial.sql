-- The API's database as the first deploy makes it: every table, each with
-- what it holds and for how long. How migrations are written and applied, and
-- why each later change is a new file: ../database.js.

-- The counts the API's limits are held to (counters.js): one row a name.
-- `day` is set for a day's count, which the nightly job deletes a week
-- later, and NULL for one kept for good, such as the bytes of every readout.
CREATE TABLE IF NOT EXISTS counters (
  name TEXT PRIMARY KEY,
  day TEXT,
  count INTEGER NOT NULL DEFAULT 0,
  bytes INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS counters_day ON counters (day);

-- The reports the tool sends at the end of a run (reports.js), each kept for
-- good; the nightly job's totals by day, model and the rest are further on.

-- One a report, as report-schema.json describes it. `day` is when it came in,
-- as a UTC date: never the time, nor anything of the sender. `device_hash`
-- is the keyed hash of the tool's hash of the device's MAC. `quarantined`: left
-- out of every figure, set by hand for reports found to be made up. Each other
-- column is the report's field of that name, a nested one's flattened: by
-- its whole path for os, python and chip (`os_arch` is os.arch,
-- `chip_flash_mb` chip.flash_mb), by its own name for computer and stopped
-- (`vpn` is computer.vpn, `step` stopped.step); a true/false one is 1/0.
CREATE TABLE IF NOT EXISTS reports (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  kind TEXT NOT NULL,
  result TEXT NOT NULL,
  step TEXT,
  error TEXT,
  text TEXT,
  family TEXT,
  device TEXT,
  model TEXT,
  stock_version TEXT,
  device_hash TEXT,
  firmware TEXT,
  source TEXT NOT NULL,
  backup TEXT NOT NULL,
  identity TEXT,
  os_family TEXT NOT NULL,
  os_version TEXT NOT NULL,
  os_arch TEXT NOT NULL,
  python_version TEXT NOT NULL,
  python_source TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  resumed INTEGER NOT NULL,
  signal INTEGER,
  wifi_typed INTEGER,
  setup_network INTEGER,
  power_cycled INTEGER,
  checks TEXT NOT NULL,
  meter TEXT,
  crash_site TEXT,
  chip_revision TEXT,
  chip_flash_id TEXT,
  chip_flash_mb INTEGER,
  connection TEXT,
  vpn INTEGER,
  networks INTEGER NOT NULL,
  security_note INTEGER NOT NULL,
  firewall TEXT,
  admin INTEGER NOT NULL,
  ewelink_region TEXT,
  ctrl_c_carried_on INTEGER NOT NULL,
  launcher TEXT NOT NULL,
  install TEXT NOT NULL,
  begun_on TEXT,
  back_after_power_cycle INTEGER,
  firmware_name TEXT,
  firmware_name_expected INTEGER,
  quarantined INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS reports_day ON reports (day);
CREATE INDEX IF NOT EXISTS reports_device_day ON reports (device_hash, day) WHERE device_hash IS NOT NULL;
-- The runs that met a device the tool does not convert yet: the public figures' devices wanted.
CREATE INDEX IF NOT EXISTS reports_unsupported ON reports (model) WHERE error = 'unsupported-device';

-- A report's stages, one row each, as its `stages` gives them: the stage's
-- ID, its time and the device's busy time in seconds, its tries, the seconds
-- the tool waited on the person, why the computer joined the device's own
-- network in it (null where it did not), and how many times the person
-- switched the device off and on in it: where those, never meant to be
-- needed, keep being needed.
-- Deleted with its report.
CREATE TABLE IF NOT EXISTS report_stages (
  id TEXT NOT NULL,
  stage TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  busy INTEGER NOT NULL,
  tries INTEGER NOT NULL,
  wait INTEGER NOT NULL,
  setup_network TEXT,
  power_cycles INTEGER NOT NULL,
  PRIMARY KEY (id, stage)
) WITHOUT ROWID;

-- The stages that needed either: the admin view's table of them, without the rest.
CREATE INDEX IF NOT EXISTS report_stages_needed ON report_stages (stage)
  WHERE setup_network IS NOT NULL OR power_cycles > 0;

-- One a device, kept for good, so a second run on it is known as the same
-- device: when it was first and last seen, how its first run ended, how many
-- runs it took to its first conversion, and how many conversions and returns
-- to Sonoff's firmware it has had. `place`: its number among the devices
-- converted on the computer that first converted it (that report's
-- nth_device), never changed once given: how many devices one computer
-- converts, with nothing that names the computer. `dev`: seen in a report
-- from the maintainer's own testing, so left out of the public figures.
CREATE TABLE IF NOT EXISTS devices (
  device_hash TEXT PRIMARY KEY,
  model TEXT,
  device TEXT,
  first_day TEXT NOT NULL,
  last_day TEXT NOT NULL,
  first_result TEXT NOT NULL,
  converted_day TEXT,
  runs INTEGER NOT NULL,
  runs_to_convert INTEGER,
  conversions INTEGER NOT NULL,
  back_to_sonoff INTEGER NOT NULL,
  firmware TEXT,
  stock_version TEXT,
  place INTEGER,
  dev INTEGER NOT NULL
) WITHOUT ROWID;

-- The maintainer's own devices, left out of every figure.
CREATE INDEX IF NOT EXISTS devices_dev ON devices (device_hash) WHERE dev = 1;

-- How many times a day /wanted was asked of each product model, and by how
-- many devices: the devices people would read out for the tool to learn.
-- Kept for good.
CREATE TABLE IF NOT EXISTS wanted_days (
  day TEXT NOT NULL,
  model TEXT NOT NULL,
  asked INTEGER NOT NULL,
  devices INTEGER NOT NULL,
  PRIMARY KEY (day, model)
) WITHOUT ROWID;

-- Each device /wanted was asked of, kept for good as `devices` is for runs:
-- its keyed hash, as a report's is kept; its model; the first and last day
-- it was asked of, and how many times.
CREATE TABLE IF NOT EXISTS wanted_devices (
  device_hash TEXT PRIMARY KEY,
  model TEXT NOT NULL,
  first_day TEXT NOT NULL,
  last_day TEXT NOT NULL,
  asks INTEGER NOT NULL
) WITHOUT ROWID;

-- By the day each was first asked of: the popularity charts, month by month.
CREATE INDEX IF NOT EXISTS wanted_devices_first_day ON wanted_devices (first_day);

-- What the nightly job (nightly.js) keeps of the reports for good: their
-- totals by day, and by month the figures a total cannot give (run times,
-- step times, what people run, the Sonoff firmware met). Each row's `tag`
-- is the report's ("user" or "dev", the maintainer's own testing), so the
-- public figures can leave the testing out. A value a report did not have
-- is '' here, as each of these columns is part of its table's key.

-- The reports of each day, counted by what they say: the public figures'
-- runs, results, routes, failures and tool versions come from here.
CREATE TABLE IF NOT EXISTS daily_totals (
  day TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  device TEXT NOT NULL,
  model TEXT NOT NULL,
  firmware TEXT NOT NULL,
  source TEXT NOT NULL,
  result TEXT NOT NULL,
  step TEXT NOT NULL,
  error TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (day, tag, tool, device, model, firmware, source, result, step, error)
) WITHOUT ROWID;

-- daily_totals, by month: what the public snapshot reads, so a night's
-- reading grows with the months kept, not the days. The nightly job makes
-- the current and the previous month's again from daily_totals each night.
CREATE TABLE IF NOT EXISTS monthly_totals (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  device TEXT NOT NULL,
  model TEXT NOT NULL,
  firmware TEXT NOT NULL,
  source TEXT NOT NULL,
  result TEXT NOT NULL,
  step TEXT NOT NULL,
  error TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (month, tag, tool, device, model, firmware, source, result, step, error)
) WITHOUT ROWID;

-- The days whose reports daily_totals counts: each folded once. Removing a
-- day here, and its totals, has the next night count it again, as after a
-- report of it was quarantined.
CREATE TABLE IF NOT EXISTS folded (
  day TEXT PRIMARY KEY
) WITHOUT ROWID;

-- How long a conversion took, in seconds, by device and route: the median
-- and the 90th percentile.
CREATE TABLE IF NOT EXISTS monthly_times (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  device TEXT NOT NULL,
  firmware TEXT NOT NULL,
  source TEXT NOT NULL,
  n INTEGER NOT NULL,
  p50 INTEGER NOT NULL,
  p90 INTEGER NOT NULL,
  PRIMARY KEY (month, tag, device, firmware, source)
) WITHOUT ROWID;

-- Each step's time, by step ID and tool version: a step that changes is a
-- new version's, so never joins another's figures.
CREATE TABLE IF NOT EXISTS monthly_steps (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  step TEXT NOT NULL,
  n INTEGER NOT NULL,
  p50 INTEGER NOT NULL,
  p90 INTEGER NOT NULL,
  PRIMARY KEY (month, tag, tool, step)
) WITHOUT ROWID;

-- The computers runs were made on: where support effort goes.
CREATE TABLE IF NOT EXISTS monthly_environment (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  os_family TEXT NOT NULL,
  python_source TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (month, tag, os_family, python_source)
) WITHOUT ROWID;

-- The Sonoff firmware versions met, by model, and how many of those runs
-- converted: an early sign of an update that closes a way in.
CREATE TABLE IF NOT EXISTS monthly_stock (
  month TEXT NOT NULL,
  tag TEXT NOT NULL,
  model TEXT NOT NULL,
  stock_version TEXT NOT NULL,
  runs INTEGER NOT NULL,
  converted INTEGER NOT NULL,
  PRIMARY KEY (month, tag, model, stock_version)
) WITHOUT ROWID;

-- Each readout kept (index.js), so that each product, firmware model and
-- version (`key`) is kept from PER_KEY different senders, more while they
-- disagree or to check them (MAX_PLACES, CHECK_DAYS), and reviewed until
-- that many agree.
-- - `sender` is a keyed hash (SENDER_KEY) of the sender's address and the
--   key, never the address: one sender's readouts of one key and offer are
--   one, and nothing ties a sender's readouts of different keys together.
-- - `fingerprint` is a hash of what every genuine readout of the key shares
--   (its firmware, not the owner's settings): readouts that agree share it.
-- - `review`: pending until READOUTS_PER_KEY of one stage agree (quorum), or
--   the maintainer approves or rejects it; a rejected one frees its place.
-- - `offer`: a hash of the SHA-256s of the files eWeLink offers the device
--   (index.js offerOf), and `offered` their versions for people ("-" for
--   none): first stages are held and agree each offer apart.
-- - `checked`: 1 for a whole one that came after some of its key were taken
--   as right (CHECK_DAYS): pending, it disagrees with them.
CREATE TABLE IF NOT EXISTS readouts (
  id TEXT PRIMARY KEY,
  key TEXT NOT NULL,
  stage TEXT NOT NULL CHECK (stage IN ('first', 'whole')),
  sender TEXT NOT NULL,
  fingerprint TEXT NOT NULL,
  bytes INTEGER NOT NULL,
  object TEXT NOT NULL,
  received TEXT NOT NULL,
  review TEXT NOT NULL DEFAULT 'pending' CHECK (review IN ('pending', 'quorum', 'approved', 'rejected')),
  offer TEXT NOT NULL DEFAULT '',
  offered TEXT NOT NULL DEFAULT '-',
  checked INTEGER NOT NULL DEFAULT 0,
  UNIQUE (key, offer, sender)
);

-- One whole readout of a key from one sender, whatever it was offered.
CREATE UNIQUE INDEX IF NOT EXISTS readouts_whole_sender ON readouts (key, sender) WHERE stage = 'whole';

CREATE INDEX IF NOT EXISTS readouts_key ON readouts (key, stage);

-- What eWeLink offers a key (`offer`, as readouts has it; `offered`, its
-- versions), taken as right on `day` (index.js OFFER_AGREES): once enough
-- readouts of it, first stages or whole ones, from enough networks, name it.
CREATE TABLE IF NOT EXISTS agreed_offers (
  key TEXT NOT NULL,
  offer TEXT NOT NULL,
  offered TEXT NOT NULL,
  day TEXT NOT NULL,
  PRIMARY KEY (key, offer)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS readouts_review ON readouts (review);

-- Reports held back for the maintainer's review (review.js).

-- Each report held, and why: it is quarantined in `reports` too, so in no
-- figure until released in the admin view (which also unfolds its day).
CREATE TABLE IF NOT EXISTS held_reports (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  why TEXT NOT NULL
) WITHOUT ROWID;

-- The store of readouts' size by day (nightly.js).

-- The readouts kept, in all, as each night found them: the admin view's
-- graph of how fast the store fills against its limit (index.js's
-- TOTAL_BYTES). Kept for good: a row a day.
CREATE TABLE IF NOT EXISTS store_days (
  day TEXT PRIMARY KEY,
  readouts INTEGER NOT NULL,
  bytes INTEGER NOT NULL
) WITHOUT ROWID;

-- readout_firmware: each offered file a readout holds, marked under
-- readouts/firmware/ (index.js), so a readout deleted takes its marks with it
-- and the next readout of the device sends the file again.
CREATE TABLE IF NOT EXISTS readout_firmware (
  readout TEXT NOT NULL,
  sha TEXT NOT NULL,
  PRIMARY KEY (readout, sha)
) WITHOUT ROWID;

-- The addresses given to be emailed as more devices are supported (index.js's
-- notify and subscribe), each under `who`, the SHA-256 of the address in
-- lower case:
-- - confirmations: one given, waiting for its owner to follow the link
--   emailed to it (token), with the devices (a JSON list of {model, stock_model},
--   [] for none) and readouts (a JSON list of their references) it named, and
--   when the link was sent (sent), so remind() sends it once more
--   REMIND_AFTER_DAYS after. Deleted when confirmed, or by the nightly job
--   SIGNUP_DAYS after (pending.js).
-- - subscribers: one confirmed, once an address, with the token of the
--   removal link every email to it carries (/forget).
-- - subscriber_devices: every device its sign-ups named, one row a model
--   and stock model, with when it was first and last named, so the owners
--   of one model are one query (tools/repo/readouts.py).
-- - subscriber_readouts: the readouts its sign-ups named, so a readout's
--   sender can be asked to try a build.
CREATE TABLE IF NOT EXISTS confirmations (
  token TEXT PRIMARY KEY,
  who TEXT NOT NULL,
  email TEXT NOT NULL,
  devices TEXT NOT NULL,
  readouts TEXT NOT NULL,
  day TEXT NOT NULL,
  sent TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS confirmations_sent ON confirmations (sent);

CREATE TABLE IF NOT EXISTS subscribers (
  who TEXT PRIMARY KEY,
  email TEXT NOT NULL,
  token TEXT NOT NULL,
  since TEXT NOT NULL,
  updated TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS subscriber_devices (
  who TEXT NOT NULL,
  model TEXT NOT NULL,
  stock_model TEXT NOT NULL,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY (who, model, stock_model)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS subscriber_devices_model ON subscriber_devices (model);

CREATE TABLE IF NOT EXISTS subscriber_readouts (
  who TEXT NOT NULL,
  readout TEXT NOT NULL,
  PRIMARY KEY (who, readout)
) WITHOUT ROWID;

-- The emails the API sends (mail.js), by day and outcome, kept for good: the
-- nightly job (nightly.js) copies each day's counts here from `counters`,
-- which keeps them a week, for the admin view's history. `code` is Email
-- Service's error code for a failed send, '' for one sent.
CREATE TABLE IF NOT EXISTS mail_days (
  day TEXT NOT NULL,
  outcome TEXT NOT NULL,
  code TEXT NOT NULL,
  n INTEGER NOT NULL,
  PRIMARY KEY (day, outcome, code)
) WITHOUT ROWID;

-- Two tables for the public figures' model names (snapshot.js), so a name
-- is shown only once several senders have given it over several days:
-- - model_senders: who named a model, as a keyed hash of the sender's
--   address and that model (reports.js), so it ties a sender to nothing
--   else, the network it came from the same way (null where unknown), and
--   the day. Deleted once the model is vouched for, or after VOUCH_KEEP_DAYS
--   (nightly.js).
-- - vouched_models: each model that had VOUCH_SENDERS senders, from
--   VOUCH_NETWORKS networks, over VOUCH_DAYS days, and the day it did, kept
--   for good.
CREATE TABLE IF NOT EXISTS model_senders (
  model TEXT NOT NULL,
  sender TEXT NOT NULL,
  network TEXT,
  day TEXT NOT NULL,
  PRIMARY KEY (model, sender, day)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS vouched_models (
  model TEXT PRIMARY KEY,
  day TEXT NOT NULL
) WITHOUT ROWID;

-- Two tables for index.js, and one for the admin view:
-- - readout_networks: the network (Cloudflare's ASN for the request) each
--   readout came from, as a keyed hash of it and the readout's key, so it ties
--   readouts of different keys together no more than their senders do.
--   Readouts agree only once they come from MIN_NETWORKS networks, and a
--   key's places past PER_KEY are kept for a network none of them came from.
--   Deleted with its readout.
-- - settings: the maintainer's switches, by name, set in the admin view:
--   email_on_agreement, "on" or "off" (on when not set).
CREATE TABLE IF NOT EXISTS readout_networks (
  id TEXT PRIMARY KEY,
  network TEXT NOT NULL
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS settings (
  name TEXT PRIMARY KEY,
  value TEXT NOT NULL
) WITHOUT ROWID;

-- The report of each device's readout (reports.js, a report with "kind":
-- "readout"), apart from the runs' reports: how readouts end, by device,
-- and how many devices the computers that make them have converted. As a
-- run's report is kept: `day` is when it came in, as a UTC date, never the
-- time nor anything of the sender; `device_hash` the keyed hash of the tool's
-- hash of the device's MAC; `quarantined`, one held for review. Kept for
-- good.
CREATE TABLE IF NOT EXISTS readout_reports (
  id TEXT PRIMARY KEY,
  day TEXT NOT NULL,
  tag TEXT NOT NULL,
  tool TEXT NOT NULL,
  result TEXT NOT NULL,
  error TEXT,
  family TEXT,
  device TEXT,
  model TEXT,
  stock_model TEXT,
  stock_version TEXT,
  whole INTEGER,
  device_hash TEXT,
  os_family TEXT NOT NULL,
  os_version TEXT NOT NULL,
  os_arch TEXT NOT NULL,
  python_version TEXT NOT NULL,
  python_source TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  converted_here INTEGER NOT NULL,
  quarantined INTEGER NOT NULL DEFAULT 0
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS readout_reports_day ON readout_reports (day);
CREATE INDEX IF NOT EXISTS readout_reports_device_day ON readout_reports (device_hash, day)
  WHERE device_hash IS NOT NULL;

-- The lists of hosts' and VPNs' networks that count toward no agreement
-- (networks.js), as the nightly cron last took each, by name: `asns`, a JSON
-- list of numbers ([] while none was taken); `taken`, the day it last was (or
-- first tried, while none was); `problem`, why the last try was not taken,
-- null when it was.
CREATE TABLE IF NOT EXISTS network_lists (
  name TEXT PRIMARY KEY,
  asns TEXT NOT NULL,
  taken TEXT NOT NULL,
  problem TEXT
) WITHOUT ROWID;
