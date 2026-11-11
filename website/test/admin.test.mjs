// admin/: the maintainer's view - only through Access, changing nothing but
// a held report, and nothing a report says read as markup.
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { verified } from "../admin/access.js";
import worker, * as admin from "../admin/index.js";
import { d1 } from "../api/local-d1.mjs";
import { writes } from "../api/reports.js";

const TEAM = "liberator";
const AUD = "a".repeat(64);
const NOW = Date.parse("2026-11-10T09:00:00Z");
const FIXTURE = JSON.parse(readFileSync(new URL("./fixtures/reports.json", import.meta.url), "utf8")).valid[0];

const { privateKey, publicKey } = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true, ["sign", "verify"]);
const jwk = { ...(await crypto.subtle.exportKey("jwk", publicKey)), kid: "k1", alg: "RS256" };
const keys = async (url) => {
  assert.equal(url, `https://${TEAM}.cloudflareaccess.com/cdn-cgi/access/certs`);
  return new Response(JSON.stringify({ keys: [jwk] }));
};
const b64 = (value) => Buffer.from(typeof value === "string" ? value : JSON.stringify(value)).toString("base64url");

async function token(claims = {}, header = {}) {
  const head = b64({ alg: "RS256", kid: "k1", ...header });
  const body = b64({ aud: [AUD], iss: `https://${TEAM}.cloudflareaccess.com`, exp: NOW / 1000 + 600, email: "owner@example.com", ...claims });
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, new TextEncoder().encode(`${head}.${body}`));
  return `${head}.${body}.${Buffer.from(signature).toString("base64url")}`;
}

const asked = (jwt, init = {}) => new Request("https://admin.firmwareliberator.com/", {
  ...init, headers: { ...(jwt ? { "Cf-Access-Jwt-Assertion": jwt } : {}), ...(init.headers ?? {}) } });
const env = (more = {}) => ({ DB: d1(), ACCESS_TEAM: TEAM, ACCESS_AUD: AUD, ...more });

test("a token Access signed for this view, in date, lets its holder in", async () => {
  assert.equal((await verified(asked(await token()), env(), NOW, keys)).email, "owner@example.com");
});

test("any other token is refused", async () => {
  const forged = (await token()).replace(/\.[^.]+$/, `.${Buffer.from("x".repeat(256)).toString("base64url")}`);
  for (const [why, jwt, bindings] of [
    ["none", null, env()],
    ["another application's", await token({ aud: ["b".repeat(64)] }), env()],
    ["another team's", await token({ iss: "https://elsewhere.cloudflareaccess.com" }), env()],
    ["expired", await token({ exp: NOW / 1000 - 1 }), env()],
    ["not yet valid", await token({ nbf: NOW / 1000 + 3600 }), env()],
    ["not signed", (await token({}, { alg: "none" })).replace(/\.[^.]+$/, "."), env()],
    ["signed by no key of Access's", await token({}, { kid: "k9" }), env()],
    ["forged", forged, env()],
    ["not a token", "not.a.token", env()],
    ["with no audience set", await token(), env({ ACCESS_AUD: "" })],
  ]) {
    assert.equal(await verified(asked(jwt), bindings, NOW, keys), null, why);
  }
});

test("without Access's token, nothing: no page, no change", async () => {
  for (const request of [asked(null), asked(null, { method: "POST" })]) {
    const response = await worker.fetch(request, env());
    assert.equal(response.status, 403);
  }
});

// A database with a report held for review, and one counted.
async function held(text = "buy cheap watches") {
  const bindings = env();
  const db = bindings.DB;
  const ok = { ...structuredClone(FIXTURE), id: randomBytes(16).toString("hex") };
  const junk = { ...structuredClone(FIXTURE), id: randomBytes(16).toString("hex"), result: "failed",
    stopped: { step: "D4", error: "upload-cut", text } };
  await db.batch(writes(db, ok, "device-1", "2026-11-09"));
  await db.batch([...writes(db, junk, "device-2", "2026-11-09", "words that read as none of the tool's messages"),
    db.prepare("INSERT INTO devices (device_hash, first_day, last_day, first_result, runs, conversions, back_to_sonoff, dev) VALUES ('device-2', '2026-11-09', '2026-11-09', 'failed', 1, 0, 0, 0)")]);
  db.db.prepare("INSERT INTO folded (day) VALUES ('2026-11-09')").run();
  return { bindings, db, junk };
}
const rows = (db, sql) => db.db.prepare(sql).all().map((row) => ({ ...row }));

test("the view shows what the database holds, as text, never markup, and is kept out of everything", async () => {
  const { bindings } = await held('<script>alert("x")</script>');
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.ok(!html.includes("<script>alert"), "a report's words are escaped");
  assert.ok(html.includes("&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;"));
  assert.match(html, /Held for review/);
  const response = await admin.handle(asked(await token()), bindings, NOW, keys);
  assert.equal(response.status, 200);
  assert.match(await response.text(), /<h1>Admin<\/h1>/);
});

test("a held report released is counted again: its day folded afresh tonight", async () => {
  const { db, junk } = await held();
  await db.batch(admin.release(db, junk.id));
  assert.deepEqual(rows(db, `SELECT quarantined FROM reports WHERE id = '${junk.id}'`), [{ quarantined: 0 }]);
  assert.deepEqual(rows(db, "SELECT * FROM held_reports"), []);
  assert.deepEqual(rows(db, "SELECT * FROM folded"), [], "counted again tonight");
});

test("a held report deleted goes, with its device's record if nothing else of it is left", async () => {
  const { db, junk } = await held();
  await db.batch(admin.remove(db, junk.id));
  assert.deepEqual(rows(db, "SELECT count(*) AS n FROM reports"), [{ n: 1 }]);
  assert.deepEqual(rows(db, "SELECT count(*) AS n FROM report_extras"), [{ n: 1 }], "its extras go with it");
  assert.deepEqual(rows(db, "SELECT device_hash FROM devices ORDER BY device_hash"), [{ device_hash: "device-1" }]);
  assert.deepEqual(rows(db, "SELECT * FROM folded"), [{ day: "2026-11-09" }], "nothing to count again");
});

test("only a report held can be released or deleted from here", async () => {
  const { db } = await held();
  const [{ id }] = rows(db, "SELECT id FROM reports WHERE quarantined = 0");
  await db.batch(admin.remove(db, id));
  await db.batch(admin.release(db, id));
  assert.deepEqual(rows(db, `SELECT count(*) AS n FROM reports WHERE id = '${id}'`), [{ n: 1 }]);
});

test("a change comes only from a form on this view itself", async () => {
  const { bindings, db, junk } = await held();
  const post = async (headers) => admin.handle(new Request("https://admin.firmwareliberator.com/held", {
    method: "POST", body: new URLSearchParams({ id: junk.id, action: "delete" }),
    headers: { "Cf-Access-Jwt-Assertion": await token(), ...headers } }), bindings, NOW, keys);
  for (const headers of [{}, { Origin: "https://evil.example" }, { Origin: admin.ORIGIN, "Sec-Fetch-Site": "cross-site" }]) {
    assert.equal((await post(headers)).status, 403, JSON.stringify(headers));
  }
  assert.deepEqual(rows(db, "SELECT count(*) AS n FROM held_reports"), [{ n: 1 }], "nothing changed");
  const done = await post({ Origin: admin.ORIGIN, "Sec-Fetch-Site": "same-origin" });
  assert.deepEqual([done.status, done.headers.get("Location")], [303, "/#held"]);
  assert.deepEqual(rows(db, "SELECT count(*) AS n FROM held_reports"), [{ n: 0 }]);
});

test("the email when readouts agree is switched off and on from the view, and only from it", async () => {
  const bindings = env();
  const set = async (value, headers = { Origin: admin.ORIGIN, "Sec-Fetch-Site": "same-origin" }, name = "email_on_agreement") =>
    admin.handle(new Request("https://admin.firmwareliberator.com/settings", { method: "POST",
      body: new URLSearchParams({ name, value }), headers: { "Cf-Access-Jwt-Assertion": await token(), ...headers } }),
    bindings, NOW, keys);
  const page = async () => admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match(await page(), /Email me when readouts come to agree: <strong>on<\/strong> <button>Turn off/, "on unless set");
  assert.equal((await set("off", { Origin: "https://evil.example" })).status, 403);
  assert.equal((await set("maybe")).status, 400);
  assert.equal((await set("off", undefined, "something_else")).status, 400);
  assert.deepEqual(rows(bindings.DB, "SELECT * FROM settings"), []);
  const done = await set("off");
  assert.deepEqual([done.status, done.headers.get("Location")], [303, "/#switches"]);
  assert.deepEqual(rows(bindings.DB, "SELECT name, value FROM settings"), [{ name: "email_on_agreement", value: "off" }]);
  assert.match(await page(), /<strong>off<\/strong> <button>Turn on/);
  await set("on");
  assert.deepEqual(rows(bindings.DB, "SELECT value FROM settings"), [{ value: "on" }]);
});

test("readouts alike from too few networks are listed, and approved from the view alone", async () => {
  const bindings = env();
  const add = bindings.DB.db.prepare(`INSERT INTO readouts (id, key, stage, sender, fingerprint, bytes, object, received)
    VALUES (?, 'NEWTHING|SN-NEW-01|1.0.0', 'whole', ?, ?, 100, 'readouts/x.tar', '2026-11-09T10:00:00Z')`);
  for (const n of [1, 2, 3]) {
    add.run(`r${n}`, `s${n}`, "f".repeat(64));
    bindings.DB.db.prepare("INSERT INTO readout_networks (id, network) VALUES (?, ?)").run(`r${n}`, n < 3 ? "a" : "b");
  }
  add.run("r4", "s4", "e".repeat(64));   // another fingerprint: not one of them
  bindings.DB.db.prepare("INSERT INTO counters (name, day, count, bytes) VALUES ('readouts:pending', NULL, 4, 400)").run();
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match(html, /Nearly agreed/);
  assert.match(html, /NEWTHING\|SN-NEW-01\|1\.0\.0<\/td><td>whole<\/td><td class="number">3<\/td><td class="number">2<\/td>/);
  const approve = async (fields, headers = { Origin: admin.ORIGIN, "Sec-Fetch-Site": "same-origin" }) =>
    admin.handle(new Request("https://admin.firmwareliberator.com/approve", { method: "POST",
      body: new URLSearchParams(fields), headers: { "Cf-Access-Jwt-Assertion": await token(), ...headers } }),
    bindings, NOW, keys);
  const group = { key: "NEWTHING|SN-NEW-01|1.0.0", stage: "whole", fingerprint: "f".repeat(64) };
  assert.equal((await approve(group, { Origin: "https://evil.example" })).status, 403);
  assert.equal((await approve({ ...group, stage: "half" })).status, 400);
  assert.equal((await approve({ ...group, fingerprint: "e".repeat(64) })).status, 303);
  assert.deepEqual(rows(bindings.DB, "SELECT review FROM readouts WHERE id = 'r4'"), [{ review: "pending" }],
    "one alone is not nearly agreed: nothing approved");
  const done = await approve(group);
  assert.deepEqual([done.status, done.headers.get("Location")], [303, "/#nearly"]);
  assert.deepEqual(rows(bindings.DB, "SELECT id, review FROM readouts ORDER BY id"),
    [{ id: "r1", review: "approved" }, { id: "r2", review: "approved" }, { id: "r3", review: "approved" },
      { id: "r4", review: "pending" }]);
  assert.deepEqual(rows(bindings.DB, "SELECT count, bytes FROM counters WHERE name = 'readouts:pending'"),
    [{ count: 1, bytes: 100 }], "their room given back");
});

test("a form not of a held report and an action is turned away", async () => {
  const { bindings } = await held();
  for (const body of [{ id: "nope", action: "delete" }, { id: "f".repeat(32), action: "drop" }]) {
    const response = await admin.handle(new Request("https://admin.firmwareliberator.com/held", {
      method: "POST", body: new URLSearchParams(body),
      headers: { "Cf-Access-Jwt-Assertion": await token(), Origin: admin.ORIGIN } }), bindings, NOW, keys);
    assert.equal(response.status, 400, JSON.stringify(body));
  }
});

test("the store's pace, and the day it would be full at it", () => {
  const day = (n) => new Date(NOW - (30 - n) * 86_400_000).toISOString().slice(0, 10);
  const store = Array.from({ length: 31 }, (_, n) => ({ day: day(n), bytes: n * 10 * 1024 * 1024 }));
  const pace = admin.pace(store, new Date(NOW));
  assert.equal(Math.round(pace.perDay / 1024 / 1024), 10);
  assert.match(pace.full, /^20\d\d-\d\d-\d\d$/);
  assert.equal(admin.pace(store.slice(0, 1), new Date(NOW)), null);
  assert.equal(admin.pace(store.map((row) => ({ ...row, bytes: 5 })), new Date(NOW)).full, null, "not filling");
});

test("the month's use: from Cloudflare's analytics, said plainly when it cannot be", async () => {
  assert.match(await admin.usage(env(), new Date(NOW)), /ANALYTICS_TOKEN/);
  const answer = (body) => async () => new Response(JSON.stringify(body));
  const bindings = env({ ANALYTICS_TOKEN: "t", ACCOUNT_ID: "a1" });
  assert.match(await admin.usage(bindings, new Date(NOW), answer({ errors: [{ message: "unknown field" }] })), /unknown field/);
  const used = await admin.usage(bindings, new Date(NOW), answer({ data: { viewer: { accounts: [{
    workers: [{ sum: { requests: 7 } }], d1: [{ sum: { rowsRead: 9, rowsWritten: 3 } }],
    r2: [{ max: { payloadSize: 5, metadataSize: 1 }, dimensions: { bucketName: "b" } }] }] } } }));
  assert.deepEqual(used, { "Workers requests this month": 7, "D1 rows read this month": 9, "D1 rows written this month": 3,
    "R2 storage, bytes": 6 });
});

test("what the plan includes is api_health.py's, and the view binds the API's database", () => {
  const health = readFileSync(new URL("../../tools/repo/api_health.py", import.meta.url), "utf8");
  for (const [what, value] of Object.entries(admin.INCLUDED)) {
    // As Python may write it: 10_000_000, or 10 * 1000 ** 3.
    const forms = [value.toLocaleString("en").replaceAll(",", "_")];
    if (value % 1000 ** 3 === 0) forms.push(`${value / 1000 ** 3} \\* 1000 \\*\\* 3`);
    assert.match(health, new RegExp(`"${what}": (${forms.join("|")}),`), what);
  }
  const id = (path) => /"database_id": "([^"]+)"/.exec(readFileSync(new URL(path, import.meta.url), "utf8"))[1];
  assert.equal(id("../admin/wrangler.jsonc"), id("../api/wrangler.jsonc"));
});

test("every answer is uncached, unframed, unindexed and runs no script", async () => {
  const response = await worker.fetch(asked(null), env());
  const csp = response.headers.get("Content-Security-Policy");
  assert.match(csp, /default-src 'none'/);
  assert.doesNotMatch(csp, /script-src/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal(response.headers.get("X-Robots-Tag"), "noindex, nofollow");
});
