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
  "0001_initial.sql": "8329e87c764023e407a84ff567cd19f7f502c879b68039637ec189566ef98862",
  "0002_offer_links.sql": "fd78305794b574ee6aba2a7144446c6d01d8839c6d0bce0b99b7e399077922ff",
  "0003_report_details.sql": "5f6751ceb29b13a5cb46b6217639422c05e45225fb441f3252e28553c71cbb00",
  "0004_report_details_as_columns.sql": "feb5a113fa27832bc8dcc06cf51688f568d92aec7a846a072a30e65df231f570",
  "0005_released_reports.sql": "c66629f23b60d8876128ac038d45262ac1fadf6ebd03fb5b3a8b68ba11b76b9a",
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

// One that only makes tables and indexes; another changes what is there, and
// is finished by hand if cut off (api/database.js).
const onlyMakes = (name) => !/^\s*(ALTER|UPDATE|DROP|INSERT|DELETE)\b/im.test(migration(name));

test("a migration that only makes tables can be run again harmlessly", () => {
  const made = migrations().filter(onlyMakes);
  assert.ok(made.includes("0001_initial.sql"));
  const db = d1({ migrated: false });
  for (const name of [...made, ...made]) {
    db.db.exec(migration(name));
  }
});

test("what migration 0003 kept in tables of their own is in its report's columns, and the tables gone", () => {
  const db = d1({ migrated: false });
  migrate(db, migrations().slice(0, 3));
  // A row of `table` with only its id: every column that must have a value given one.
  const row = (table, id) => {
    const needed = db.db.prepare(`SELECT name, type FROM pragma_table_info('${table}')
      WHERE "notnull" AND dflt_value IS NULL AND name != 'id'`).all();
    db.db.prepare(`INSERT INTO ${table} (id, ${needed.map((column) => column.name).join(", ")})
      VALUES (?, ${needed.map((column) => (column.type === "INTEGER" ? "0" : "'x'")).join(", ")})`).run(id);
  };
  row("reports", "run");
  row("reports", "plain");
  row("readout_reports", "readout");
  db.db.exec(`INSERT INTO stop_questions VALUES ('run', 'go-ahead'), ('readout', 'which-device');
    INSERT INTO stock_checks VALUES ('run', 'cannot-tell'), ('readout', 'sonoff-answers');
    INSERT INTO refused_tables VALUES ('run', 'abcd', '0x8000');
    INSERT INTO readout_steps VALUES ('readout', 'probe');`);
  migrate(db, ["0004_report_details_as_columns.sql"]);
  const all = (sql) => db.db.prepare(sql).all().map((found) => ({ ...found }));
  assert.deepEqual(all("SELECT id, question, stock_check, table_refused_sha256, table_refused_at FROM reports ORDER BY id"), [
    { id: "plain", question: null, stock_check: null, table_refused_sha256: null, table_refused_at: null },
    { id: "run", question: "go-ahead", stock_check: "cannot-tell", table_refused_sha256: "abcd", table_refused_at: "0x8000" }]);
  assert.deepEqual(all("SELECT id, step, question, stock_check FROM readout_reports"),
    [{ id: "readout", step: "probe", question: "which-device", stock_check: "sonoff-answers" }]);
  assert.deepEqual(all(`SELECT name FROM sqlite_master WHERE name IN ('stop_questions', 'stock_checks',
    'refused_tables', 'readout_steps')`), []);
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
