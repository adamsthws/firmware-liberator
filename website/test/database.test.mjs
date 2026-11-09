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
  "0002_reports.sql": "92ececb36c841fa2526e0541d44b7102c6bbf276bdea89b8668ab3d3623bbae3",
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
