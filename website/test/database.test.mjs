// api/database.js and api/migrations/: the newest migration is the one the
// code waits for, none changes once written, each can be run again, and the
// Worker knows a database not yet brought up to date.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { LATEST_MIGRATION, ready } from "../api/database.js";
import { d1, migrate, migration, migrations } from "../api/local-d1.mjs";

// Each migration's SHA-256 as it was written. One applied to the live
// database is never changed (api/database.js says why): a change is a new
// file, whose hash goes here once it is final.
const WRITTEN = {
  "0001_counters.sql": "db8a1672da5fc1b696fb61412d7c7d5ebfbffffab60e88e555ab23a1eed08f23",
  "0002_reports.sql": "5a41c030f2f7cbab12faba9a09428d5850707d857293b308fbc2a425a68d02f6",
  "0003_totals.sql": "aa0200ac16c5e5e40e927121ac6388f35c2a05256ec7c14afe300f1d3ddef563",
  "0004_readouts.sql": "eff10d3be7755b62a242aa940ca197b3e34221032c0074087292a4f2bc2faedc",
  "0005_held_reports.sql": "08b89131937fcd4bd97e33644212c2404f4bbff56c25423eea22271307d84f50",
  "0006_store_days.sql": "d2cfd8f1d253dd7cee22c09cd0cd19da3237af932ef0dd134c51032e8eef5ccf",
  "0007_readout_firmware.sql": "e8a39e3308c3f477b14b77dc8f21fec15b9698a7d32a565ffe644ae8d01e8923",
  "0008_confirmations.sql": "9f950a1fc32c7862fae5e9edf019627409e032c35bd583fa516fbfff26737358",
  "0009_mail_days.sql": "cd65431a174bfa5a3299fbf63aa7960a675b7b3fc2f460acfc3d331aa4ba0465",
  "0010_model_senders.sql": "430681629148038603e98a5d0410e098b65936fd7ac80757e730b525e94e1ab4",
  "0011_networks_and_settings.sql": "5ce944c5534eb3315feb64bd43524931eabcdd5a3a0d114238b1ce4c2a51d402",
  "0012_readout_reports.sql": "e769c9d5e980df7556a2d1d9061af90b45eee41d9826da5467b3dfc1d5940f7f",
};

test("the migrations are numbered one after another, from 0001", () => {
  migrations().forEach((name, index) => {
    assert.match(name, /^\d{4}_[a-z0-9_]+\.sql$/, name);
    assert.equal(Number(name.slice(0, 4)), index + 1, name);
  });
});

test("the newest migration is the one the code waits for", () => {
  assert.equal(LATEST_MIGRATION, migrations().at(-1));
});

test("no migration changes once written", () => {
  for (const name of migrations()) {
    const hash = createHash("sha256").update(migration(name)).digest("hex");
    assert.equal(hash, WRITTEN[name], `${name}: changed, or new without its hash in WRITTEN`);
  }
  assert.deepEqual(Object.keys(WRITTEN), migrations(), "a migration in WRITTEN has no file");
});

test("each migration can be run again harmlessly", () => {
  const db = d1();
  for (const name of migrations()) {
    db.db.exec(migration(name));
  }
});

test("a database is ready only once it has the newest migration", async () => {
  assert.equal(await ready(d1({ migrated: false })), false, "never migrated");
  const partly = d1({ migrated: false });
  migrate(partly, migrations().slice(0, -1));
  assert.equal(await ready(partly), false, "one migration short");
  const db = d1({ migrated: false });
  assert.equal(await ready(db, 0), false);
  migrate(db);
  assert.equal(await ready(db, 1), true, "not ready is never kept: asked again at once");
});
