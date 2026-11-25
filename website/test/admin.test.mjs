// admin/: the maintainer's view - only through Access, changing nothing but
// by its own forms, and nothing a report or a link says read as markup.
import assert from "node:assert/strict";
import { createHash, randomBytes } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { verified } from "../admin/access.js";
import worker, * as admin from "../admin/index.js";
import { d1 } from "../api/local-d1.mjs";
import { COUNTER_DAYS } from "../api/nightly.js";
import { linksNotKeptOn, PENDING_LINKS_MOST, PENDING_LINKS_PER_SENDER } from "../api/offered.js";
import { writes } from "../api/reports.js";
import { SITE, THEME_SCRIPT } from "../src/shell.mjs";

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
  const ok = { ...structuredClone(FIXTURE), id: randomBytes(8).toString("hex") };
  const junk = { ...structuredClone(FIXTURE), id: randomBytes(8).toString("hex"), result: "failed",
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

test("the view has the site's look, its theme's script served from here, to the signed-in alone", async () => {
  const { bindings } = await held();
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match(html, /<article class="markdown-body">\n<div class="admin">/);
  assert.ok(html.includes(`"${SITE}/assets/github-markdown-dark.css"`));
  assert.match(html, /<script src="\/assets\/theme\.js"><\/script>/);
  assert.match(html, /<button id="theme" type="button"/);
  const script = (jwt) => admin.handle(new Request("https://admin.firmwareliberator.com/assets/theme.js",
    { headers: jwt ? { "Cf-Access-Jwt-Assertion": jwt } : {} }), bindings, NOW, keys);
  const served = await script(await token());
  assert.equal(served.headers.get("Content-Type"), "text/javascript; charset=utf-8");
  assert.equal(await served.text(), THEME_SCRIPT);
  assert.equal((await script(null)).status, 403);
});

test("a held report and a recent one say the device kind as well as the model", async () => {
  const { bindings } = await held();
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  const section = (id) => html.split(`id="${id}">`)[1].split("</section>")[0];
  assert.match(section("held"), /<th>Device<\/th><th>Model<\/th>/);
  assert.match(section("held"), /<td>Sonoff S60<\/td><td>S60TPG<\/td>/);
  assert.match(html.split("The latest reports")[1], /<td>Sonoff S60<\/td><td>S60TPG<\/td>/);
});

test("the latest reports and readout reports say the question each was stopped at, a readout its stage too", async () => {
  const bindings = env();
  const samples = JSON.parse(readFileSync(new URL("./fixtures/reports.json", import.meta.url), "utf8"));
  const run = samples.valid.find((sample) => sample.stopped?.question);
  const readout = samples.readoutValid.find((sample) => sample.stopped?.question);
  await bindings.DB.batch([...writes(bindings.DB, run, "a".repeat(64), "2026-11-09"),
    ...writes(bindings.DB, readout, "b".repeat(64), "2026-11-09")]);
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match(html.split("The latest reports")[1].split("</section>")[0],
    new RegExp(`<td>${run.stopped.step} quit at ${run.stopped.question}</td>`));
  assert.match(html.split('id="readout-reports">')[1].split("</section>")[0],
    new RegExp(`<td>cancelled</td><td>${readout.stopped.step} at ${readout.stopped.question}</td>`));
});

test("the latest reports, readout reports and held ones say what the check of Sonoff's firmware found", async () => {
  const bindings = env();
  const samples = JSON.parse(readFileSync(new URL("./fixtures/reports.json", import.meta.url), "utf8"));
  const run = samples.valid.find((sample) => sample.stock_check);
  const readout = samples.readoutValid.find((sample) => sample.stock_check);
  await bindings.DB.batch([...writes(bindings.DB, run, "a".repeat(64), "2026-11-09"),
    ...writes(bindings.DB, readout, "b".repeat(64), "2026-11-09"),
    ...writes(bindings.DB, { ...run, id: "fedcba9876543210", stock_check: "neither-answers" }, "c".repeat(64),
      "2026-11-09", "held for review")]);
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  const card = (heading) => html.split(heading)[1].split("</section>")[0];
  assert.match(card("The latest reports"), new RegExp(`<td>${run.stock_check}</td>`));
  assert.match(card('id="readout-reports">'), new RegExp(`<td>${readout.stock_check}</td>`));
  assert.match(card('id="held">'), /<td>neither-answers<\/td>/);
});

test("the view lists the keys a later whole readout disagrees with, unsettled", async () => {
  const { bindings, db } = await held();
  const add = db.db.prepare(`INSERT INTO readouts (id, key, stage, sender, fingerprint, bytes, object, received, review,
    checked) VALUES (?, 'NEWTHING|SN-NEW-01|1.0.0', 'whole', ?, ?, 100, 'readouts/x.tar', ?, ?, ?)`);
  for (const n of [1, 2, 3]) {
    add.run(`r${n}`, `s${n}`, "f".repeat(64), "2026-10-01T10:00:00Z", "quorum", 0);
  }
  add.run("r4", "s4", "f".repeat(64), "2026-11-01T10:00:00Z", "quorum", 1);   // a check alike
  const disputed = async () => (await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys))
    .split('id="disputed">')[1].split("</section>")[0];
  assert.match(await disputed(), /<p class="none">None\.<\/p>/, "a check alike is no dispute");
  add.run("r5", "s5", "e".repeat(64), "2026-11-08T10:00:00Z", "pending", 1);
  assert.match(await disputed(), /NEWTHING\|SN-NEW-01\|1\.0\.0<\/td><td>whole<\/td><td class="number">4<\/td><td class="number">1<\/td><td>2026-11-08<\/td>/);
});

test("the view lists the offers readouts agreed on", async () => {
  const { bindings, db } = await held();
  db.db.prepare("INSERT INTO agreed_offers (key, offer, offered, day) VALUES ('NEWTHING|SN-NEW-01|1.0.0', 'x', '1.2.0', '2026-11-09')")
    .run();
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match(html.split('id="offers">')[1], /^[^]*?<td>NEWTHING\|SN-NEW-01\|1\.0\.0<\/td><td>1\.2\.0<\/td><td>2026-11-09<\/td>/);
});

test("the view counts offers sent as links, by key, the files fetched from them, and those still missing", async () => {
  const { bindings, db } = await held();
  const section = async () => (await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys))
    .split('id="links">')[1].split("</section>")[0];
  assert.match(await section(), /<p class="none">None yet\.<\/p>/);
  const readout = db.db.prepare(`INSERT INTO readouts (id, key, stage, sender, fingerprint, bytes, object, received, review,
    offer) VALUES (?, ?, 'first', ?, 'o', 100, 'readouts/x.json', '2026-11-09T10:00:00Z', ?, 'o')`);
  const link = db.db.prepare("INSERT INTO offer_links (readout, sha, path, cloud, bytes) VALUES (?, ?, '/ota/rom/x/y', 'ewelink', ?)");
  readout.run("a1", "A|SN-A|1.0", "s1", "pending");
  readout.run("a2", "A|SN-A|1.0", "s2", "pending");
  readout.run("b1", "B|SN-B|1.0", "s1", "quorum");
  link.run("a1", "1".repeat(64), 2048);
  db.db.prepare("INSERT INTO readout_firmware (readout, sha) VALUES ('a1', ?)").run("1".repeat(64));
  link.run("a2", "1".repeat(64), null);
  link.run("b1", "2".repeat(64), null);
  link.run("b1", "3".repeat(64), null);
  db.db.prepare("INSERT INTO agreed_offers (key, offer, offered, day) VALUES ('B|SN-B|1.0', 'o', '1.2.0', '2026-11-09')").run();
  db.db.prepare("INSERT INTO counters (name, day, count) VALUES ('offer-fetches:2026-11-10', '2026-11-10', 7)").run();
  const html = await section();
  const tileOf = (label) => html.split(`<div class="tile-label">${label}</div>`)[1].split('<div class="tile">')[0];
  assert.match(tileOf("Sent as links"), /tile-value">3<\/div><\/div>/);
  assert.match(tileOf("Offers agreed"), /tile-value">1<\/div><\/div>/);
  assert.match(tileOf("Files fetched from links"), /tile-value">1<\/div><div class="tile-note">2\.0 KB/);
  assert.match(tileOf("Files missing"), /tile-value">2<\/div><div class="tile-note">for 1 agreed offers/);
  assert.match(tileOf("Fetches today"), /tile-value">7<\/div><div class="tile-note">of 50 a day/);
  assert.match(html, /<td>A\|SN-A\|1\.0<\/td><td class="number">2<\/td><td class="number">0<\/td><td class="number">1<\/td><td>2\.0 KB<\/td>/);
  assert.match(html, /<td>B\|SN-B\|1\.0<\/td><td class="number">1<\/td><td class="number">1<\/td><td class="number">0<\/td><td>0 B<\/td>/);
  assert.match(html.split("Files missing for an agreed offer")[1],
    /<td>B\|SN-B\|1\.0<\/td><td>1\.2\.0<\/td><td>2026-11-09<\/td><td class="number">2<\/td>/);
});

// A run's report with the offers sent beside it, as /reports writes it, on `day`.
const WITH_OFFERS = JSON.parse(readFileSync(new URL("./fixtures/reports.json", import.meta.url), "utf8")).valid
  .find((report) => report.offers);
async function offered(db, day, offers = {}, changes = {}, held = null) {
  const report = { ...structuredClone(WITH_OFFERS), ...changes, id: randomBytes(8).toString("hex") };
  report.offers = { ...report.offers, ...offers };
  await db.batch(writes(db, report, null, day, held));
  return report.id;
}

test("the view shows the offers sent beside runs, day by day: sent, taken, and the sign-in ended", async () => {
  const bindings = env();
  const db = bindings.DB;
  const view = async () => (await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys))
    .split('id="offer-runs">')[1].split("</section>")[0];
  assert.match(await view(), /None yet\./);
  for (let n = 0; n < 4; n += 1) {
    await offered(db, "2026-11-08", { sent: 3, taken: 2, session_ended: n === 0, run_session_ended: n < 2 });
  }
  await offered(db, "2026-11-09", { sent: 1, taken: 1, session_ended: false, run_session_ended: null });
  await offered(db, "2026-11-09", { requests: 0, sent: 0, taken: 0, why_not: "none-due" });   // asked nothing
  await offered(db, "2026-11-09", { sent: 50, taken: 50 }, { tag: "dev" });   // your own testing
  await offered(db, "2026-11-09", { sent: 50, taken: 50 }, {}, "held for review");
  await offered(db, "2026-10-01", { sent: 50, taken: 50 });   // before the last OFFER_DAYS
  const section = await view();
  assert.ok(section.includes('<div class="tile-label">Runs that asked</div><div class="tile-value">5</div>'), section);
  assert.ok(section.includes('<div class="tile-label">Offers sent</div><div class="tile-value">13</div>'
    + '<div class="tile-note">9 taken</div>'), section);
  assert.ok(section.includes('<div class="tile-value">20%</div><div class="tile-note">1 of 5 runs</div>'));
  assert.ok(section.includes('<div class="tile-value">50%</div><div class="tile-note">2 of 4 runs</div>'),
    "of those whose run could tell");
  assert.ok(section.includes('<tr><td>2026-11-09</td><td class="number">1</td><td class="number">1</td>'
    + '<td class="number">1</td><td>0%</td><td>-</td></tr><tr><td>2026-11-08</td><td class="number">4</td>'
    + '<td class="number">12</td><td class="number">8</td><td>25%</td><td>50%</td></tr>'), "newest first, every day");
});

test("a held report deleted takes what it said of its offers, a table refused and its stock check with it", async () => {
  const db = env().DB;
  const id = await offered(db, "2026-11-09", {}, { table_refused: { sha256: "ab".repeat(32), at: "0x8000" },
    stock_check: "neither-answers" }, "held for review");
  assert.equal(rows(db, "SELECT id FROM reports WHERE table_refused_at = '0x8000' AND stock_check = 'neither-answers'").length, 1);
  await db.batch(admin.remove(db, id));
  assert.deepEqual(rows(db, "SELECT id FROM report_offers"), []);
  assert.deepEqual(rows(db, "SELECT id FROM reports"), []);
});

test("the view shows each list of networks that count as none: its size, when taken, and the last try", async () => {
  const { bindings, db } = await held();
  assert.match(await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys), /Not fetched yet\./);
  db.db.prepare(`INSERT INTO network_lists (name, asns, taken, problem, previous)
    VALUES ('vpn', '[64500,64501]', '2026-11-02', 'HTTP 404', 1)`).run();
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match(html, /<td>vpn<\/td><td class="number">2<\/td><td class="number">1<\/td><td>2026-11-02<\/td><td>HTTP 404<\/td>/);
});

test("the view shows the tool's downloads, one total beside the devices converted, each night GitHub was read", async () => {
  const { bindings, db } = await held();
  const view = async () => admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match(await view(), /GitHub not read yet/);
  assert.match((await view()).split('id="downloads">')[1].split("</section>")[0], /Not enough days yet\./);
  const read = db.db.prepare("INSERT INTO download_days (day, downloads) VALUES (?, ?)");
  read.run("2026-11-08", 1210);
  read.run("2026-11-09", 1312);
  const html = await view();
  assert.ok(html.includes('<div class="tile-label">Downloads</div><div class="tile-value">1,312</div>'
    + '<div class="tile-note">to 2026-11-09</div>'));
  db.db.prepare("INSERT INTO download_misses (day, problem, rate_limited) VALUES ('2026-11-08', 'HTTP 500', 0)").run();
  assert.ok((await view()).includes('<div class="tile-note">to 2026-11-09</div>'), "a night refused before the last read is past");
  db.db.prepare("INSERT INTO download_misses (day, problem, rate_limited) VALUES ('2026-11-10', 'GitHub''s rate limit reached (HTTP 403)', 1)").run();
  assert.ok((await view()).includes('<div class="tile-note">to 2026-11-09 · refused for 2026-11-10: '
    + 'GitHub&#39;s rate limit reached (HTTP 403)</div>'));
  const chart = html.split('id="downloads">')[1].split("</section>")[0];
  assert.match(chart, /<title>2026-11-09: 1,312 downloads<\/title>/);
  assert.match(chart, /<title>2026-11-09: \d+ devices converted<\/title>/);
});

test("the view counts your users' runs and devices in all and by system, your own and held reports left out", async () => {
  const bindings = env();
  const db = bindings.DB;
  const run = async (hash, os, result = "converted", tag = "user") => db.batch(writes(db,
    { ...structuredClone(FIXTURE), id: randomBytes(8).toString("hex"), tag, result, os: { ...FIXTURE.os, family: os },
      stopped: result === "converted" ? null : { step: "D4", error: "upload-cut", text: "" } }, hash, "2026-11-09"));
  const view = async () => admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match((await view()).split('id="downloads">')[1].split("</section>")[0], /No runs yet\./);
  await run("device-1", "windows", "failed");
  await run("device-6", "macos", "cancelled");   // stopped by the person: a run, no device seen
  await run("device-1", "windows");
  await run("device-2", "macos", "failed");
  await run("device-2", "linux");   // the same device, from a second system
  await run("device-3", "linux");
  await run("device-4", "linux", "converted", "dev");
  db.db.prepare("UPDATE devices SET dev = 1 WHERE device_hash = 'device-4'").run();
  await run("device-4", "linux");   // the maintainer's device, though tagged as a user's
  await run(null, "windows", "failed");   // a run that met no device
  const held = { ...structuredClone(FIXTURE), id: randomBytes(8).toString("hex"), os: { ...FIXTURE.os, family: "macos" } };
  await db.batch(writes(db, held, "device-5", "2026-11-09"));
  db.db.prepare("UPDATE reports SET quarantined = 1 WHERE id = ?").run(held.id);
  const html = await view();
  const row = (name, runs, seen, converted) => `<tr><td>${name}</td><td class="number">${runs}</td>`
    + `<td class="number">${seen}</td><td class="number">${converted}</td></tr>`;
  const section = html.split('id="downloads">')[1].split("</section>")[0];
  assert.ok(section.includes(row("All systems", 7, 3, 3) + row("Windows", 3, 1, 1) + row("Linux", 2, 2, 2)
    + row("macOS", 2, 1, 0)), section);
  assert.ok(html.includes('<div class="tile-label">Devices seen, by system</div><div class="tile-value">3</div>'
    + '<div class="tile-note">Windows 1 · Linux 2 · macOS 1</div>'), "macOS's ties Linux's runs, so after it");
});

test("a held report released is counted again: its day folded afresh tonight", async () => {
  const { db, junk } = await held();
  await db.batch(admin.release(db, junk.id));
  assert.deepEqual(rows(db, `SELECT quarantined, released FROM reports WHERE id = '${junk.id}'`),
    [{ quarantined: 0, released: 1 }], "and never held as a spike's again");
  assert.deepEqual(rows(db, "SELECT * FROM held_reports"), []);
  assert.deepEqual(rows(db, "SELECT * FROM folded"), [], "counted again tonight");
});

test("a held report deleted goes, with its device's record if nothing else of it is left", async () => {
  const { db, junk } = await held();
  await db.batch(admin.remove(db, junk.id));
  assert.deepEqual(rows(db, "SELECT count(*) AS n FROM reports"), [{ n: 1 }]);
  assert.deepEqual(rows(db, "SELECT DISTINCT id FROM report_stages"), rows(db, "SELECT id FROM reports"),
    "its stages go with it");
  assert.deepEqual(rows(db, "SELECT device_hash FROM devices ORDER BY device_hash"), [{ device_hash: "device-1" }]);
  assert.deepEqual(rows(db, "SELECT * FROM folded"), [{ day: "2026-11-09" }], "nothing to count again");
});

// Reports of `device` on `day` held for `why`, as the nightly job holds a spike's.
async function heldAs(db, device, day, why, count, model = "S60TPF") {
  for (let n = 0; n < count; n += 1) {
    const report = { ...structuredClone(FIXTURE), id: randomBytes(8).toString("hex"), device, model };
    await db.batch(writes(db, report, `${device}-${day}-${n}`, day));
    db.db.prepare("INSERT INTO held_reports (id, day, why) VALUES (?, ?, ?)").run(report.id, day, why);
    db.db.prepare("UPDATE reports SET quarantined = 1 WHERE id = ?").run(report.id);
  }
}

test("all of one device's held on one day for one reason are released at once, and no others", async () => {
  const { bindings, db, junk } = await held();
  const spike = "a day far above usual for its device";
  await heldAs(db, "sonoff-s60", "2026-11-09", spike, 3);
  await heldAs(db, "sonoff-s61", "2026-11-09", spike, 2);
  await heldAs(db, "sonoff-s60", "2026-11-08", spike, 1);
  // One of no kind is counted by its model, as the nightly job counts it.
  await heldAs(db, null, "2026-11-09", spike, 2, "NEWTHING");
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.ok(html.includes("<button>Release all 3</button>"), "a button for each");
  assert.ok(html.includes('<input type="hidden" name="named" value="NEWTHING">'));
  assert.match(html, /Held for review<\/div><div class="tile-value">9</);
  const post = async (fields, headers = { Origin: admin.ORIGIN, "Sec-Fetch-Site": "same-origin" }) =>
    admin.handle(new Request("https://admin.firmwareliberator.com/held", { method: "POST",
      body: new URLSearchParams(fields), headers: { "Cf-Access-Jwt-Assertion": await token(), ...headers } }),
    bindings, NOW, keys);
  const all = { action: "release-all", day: "2026-11-09", why: spike, named: "sonoff-s60" };
  assert.equal((await post(all, { Origin: "https://evil.example" })).status, 403);
  assert.equal((await post({ ...all, day: "yesterday" })).status, 400);
  const done = await post(all);
  assert.deepEqual([done.status, done.headers.get("Location")], [303, "/#held"]);
  const left = rows(db, `SELECT reports.device, reports.model, held_reports.day FROM held_reports
    JOIN reports ON reports.id = held_reports.id ORDER BY 1, 2, 3`);
  assert.deepEqual(left.map((row) => `${row.device}/${row.model}/${row.day}`), ["null/NEWTHING/2026-11-09",
    "null/NEWTHING/2026-11-09", "sonoff-s60/S60TPG/2026-11-09", "sonoff-s60/S60TPF/2026-11-08",
    "sonoff-s61/S60TPF/2026-11-09", "sonoff-s61/S60TPF/2026-11-09"].sort(),
  "the junk report held for another reason, the other day's, the other device's and the model's stay");
  assert.deepEqual(rows(db, `SELECT count(*) AS n FROM reports WHERE device = 'sonoff-s60' AND day = '2026-11-09'
    AND model = 'S60TPF' AND quarantined = 0`), [{ n: 3 }], "counted again");
  assert.deepEqual(rows(db, "SELECT * FROM folded"), [], "their day counted again tonight");
  assert.ok(rows(db, `SELECT id FROM held_reports WHERE id = '${junk.id}'`).length);
  await post({ action: "release-all", day: "2026-11-09", why: spike, named: "NEWTHING" });
  assert.deepEqual(rows(db, "SELECT count(*) AS n FROM held_reports WHERE day = '2026-11-09'"), [{ n: 3 }]);
});

test("a readout's report held is listed, counted, and released or deleted as a run's is", async () => {
  const { bindings, db } = await held();
  const sample = JSON.parse(readFileSync(new URL("./fixtures/reports.json", import.meta.url), "utf8")).readoutValid[0];
  const readout = (model) => ({ ...structuredClone(sample), id: randomBytes(8).toString("hex"), model });
  const [one, other] = [readout("HELDONE"), readout("HELDTWO")];
  for (const report of [one, other]) {
    await db.batch(writes(db, report, null, "2026-11-09", "words no report would hold"));
  }
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  const section = html.split('id="held">')[1].split("</section>")[0];
  assert.ok(section.includes("<td>readout</td>") && section.includes("HELDONE") && section.includes("HELDTWO"), section);
  assert.match(html, /Held for review<\/div><div class="tile-value">3</, "the run's and the two readouts'");
  await db.batch(admin.release(db, one.id));
  assert.deepEqual(rows(db, `SELECT quarantined FROM readout_reports WHERE id = '${one.id}'`), [{ quarantined: 0 }]);
  await db.batch(admin.remove(db, other.id));
  assert.deepEqual(rows(db, `SELECT id FROM readout_reports WHERE id = '${other.id}'`), []);
  assert.deepEqual(rows(db, "SELECT count(*) AS n FROM held_reports"), [{ n: 1 }], "the run's alone is left");
});

test("only a report held can be released or deleted from here", async () => {
  const { db } = await held();
  const [{ id }] = rows(db, "SELECT id FROM reports WHERE quarantined = 0");
  await db.batch(admin.remove(db, id));
  await db.batch(admin.release(db, id));
  assert.deepEqual(rows(db, `SELECT count(*) AS n FROM reports WHERE id = '${id}'`), [{ n: 1 }]);
});

test("the view's own forms send its origin: a browser gives Origin: null under no-referrer", async () => {
  const { bindings } = await held();
  const response = await admin.handle(asked(await token()), bindings, NOW, keys);
  // Fetch's "append a request Origin header": a POST that is not CORS is sent
  // with Origin: null under no-referrer, which ownForm refuses as not this view's.
  assert.equal(response.headers.get("Referrer-Policy"), "same-origin");
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
  assert.match(html, /NEWTHING\|SN-NEW-01\|1\.0\.0<\/td><td>whole<\/td><td><\/td><td class="number">3<\/td><td class="number">2<\/td>/,
    "a whole readout's offer is not what it is agreed on");
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
  for (const body of [{ id: "nope", action: "delete" }, { id: "f".repeat(16), action: "drop" }]) {
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

test("every answer is uncached, unframed, unindexed and runs no script but its own, the theme's and the copy button's", async () => {
  const response = await worker.fetch(asked(null), env());
  const csp = response.headers.get("Content-Security-Policy");
  assert.match(csp, /default-src 'none'/);
  assert.match(csp, /(^|; )script-src 'self'(;|$)/);
  assert.match(csp, /frame-ancestors 'none'/);
  assert.equal(response.headers.get("Cache-Control"), "no-store");
  assert.equal(response.headers.get("X-Robots-Tag"), "noindex, nofollow");
});

test("each model's devices month by month: asked to be read out, read out, converted", async () => {
  const bindings = env();
  const db = bindings.DB.db;
  const wanted = db.prepare("INSERT INTO wanted_devices (device_hash, model, first_day, last_day, asks) VALUES (?, ?, ?, ?, 1)");
  wanted.run("a", "NEWTHING", "2026-09-02", "2026-11-01");
  wanted.run("b", "NEWTHING", "2026-11-03", "2026-11-03");
  wanted.run("c", "NEWTHING", "2024-01-01", "2024-01-01");   // before the charts begin
  const readOut = db.prepare(`INSERT INTO readout_reports (id, day, tag, tool, result, model, device_hash, os_family,
    os_version, os_arch, python_version, python_source, seconds, converted_here, quarantined)
    VALUES (?, ?, ?, '3.0.0', 'accepted', 'NEWTHING', ?, 'linux', 'x', 'x86-64', '3.12.3', 'venv', 60, 0, ?)`);
  readOut.run("1", "2026-11-04", "user", "a", 0);
  readOut.run("2", "2026-11-05", "user", "a", 0);   // the same device again that month: one
  readOut.run("3", "2026-11-05", "dev", "d", 0);   // the maintainer's own testing
  readOut.run("4", "2026-11-05", "user", "e", 1);   // quarantined
  const run = (id, hash, day, changes = {}) => bindings.DB.batch(writes(bindings.DB,
    { ...structuredClone(FIXTURE), id, model: "S60TPG", ...changes }, hash, day));
  await run("r1", "f", "2026-10-01");
  await run("r2", "g", "2026-10-02");
  await run("r3", "g", "2026-10-20", { firmware: "esphome", source: "tasmota" });   // converted again: one that month
  await run("r4", "g", "2026-11-02", { firmware: "esphome", source: "tasmota" });   // and again the next
  await run("r5", "h", "2026-10-03", { firmware: "sonoff", source: "tasmota" });   // back to Sonoff's: no conversion
  await run("r6", "i", "2026-10-04", { tag: "dev" });   // the maintainer's own
  const { months, models } = await admin.gather(bindings, new Date(NOW)).then((data) => data.popularity);
  assert.deepEqual([months.length, months[0], months.at(-1)], [admin.POPULAR_MONTHS, "2024-12", "2026-11"]);
  assert.deepEqual(models.map(({ model, series }) => [model, series]), [
    ["NEWTHING", { "asked to be read out": { "2026-09": 1, "2026-11": 1 }, "read out": { "2026-11": 1 } }],
    ["S60TPG", { converted: { "2026-10": 2, "2026-11": 1 } }]]);
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match(html, /<figcaption>NEWTHING<\/figcaption>/);
  assert.match(html, /<title>S60TPG, 2026-10: 2 converted<\/title>/);
});

test("the stages that needed the device's own network, and why, or a power cycle, out of the runs through them", async () => {
  const bindings = env();
  const stages = (d4) => ({ A1: { seconds: 12, busy: 0, tries: 1, wait: 0, setup_network: null, power_cycles: 0 },
    D4: { seconds: 61, busy: 0, tries: 1, wait: 0, power_cycles: 0, setup_network: null, ...d4 } });
  const run = (id, d4, changes = {}) => bindings.DB.batch(writes(bindings.DB,
    { ...structuredClone(FIXTURE), id, stages: stages(d4), ...changes }, `device-${id}`, "2026-11-01"));
  await run("r1", { setup_network: "not given", power_cycles: 2 });
  await run("r2", { setup_network: "not joined" });
  await run("r3", {});
  await run("r4", { setup_network: "not given" }, { tag: "dev" });   // the maintainer's own: left out
  const { needed } = await admin.gather(bindings, new Date(NOW));
  const why = (reason) => `why${admin.REASONS.indexOf(reason)}`;
  assert.deepEqual(needed.map((row) => [row.stage, row.runs, row.setup, row[why("not given")], row[why("not joined")],
    row.cycled, row.cycles]), [["D4", 3, 2, 1, 1, 1, 2]], "A1 needed neither");
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.match(html, /Setup network and power cycles, by stage/);
  assert.match(html, /<th class="number">- settings lost<\/th>/);
});

test("the fortnight's reports by how each run ended, every run result counted once, held ones in none", async () => {
  const schema = JSON.parse(readFileSync(new URL("../api/report-schema.json", import.meta.url), "utf8"));
  assert.deepEqual(admin.OUTCOMES.flatMap(([, results]) => results).sort(), [...schema.properties.result.enum].sort());
  const bindings = env();
  const run = (id, result, day = "2026-11-09") => bindings.DB.batch(writes(bindings.DB,
    { ...structuredClone(FIXTURE), id, result }, `device-${id}`, day));
  await run("r1", "converted");
  await run("r2", "updated");
  await run("r3", "failed");
  await run("r4", "failed");
  await run("r5", "cancelled");
  await run("r6", "crashed", "2026-11-08");
  await run("r7", "failed");
  bindings.DB.db.prepare("UPDATE reports SET quarantined = 1 WHERE id = 'r7'").run();
  const { week } = await admin.gather(bindings, new Date(NOW));
  assert.deepEqual(week.map((row) => [row.day, row.o0, row.o1, row.o2, row.o3]),
    [["2026-11-08", 0, 0, 1, 0], ["2026-11-09", 2, 2, 0, 1]]);
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  for (const said of ["2026-11-09: 2 converted", "2026-11-09: 2 failed", "2026-11-09: 1 cancelled", "2026-11-08: 1 crashed"]) {
    assert.match(html, new RegExp(`<title>${said}</title>`));
  }
});

// Links to review (api/offered.js): listed with what was seen of them, every
// part escaped, each link in a copy field alone, approved or rejected only
// from the view itself.
const SHA = "a".repeat(64);
const READOUT = "2026-11-09-0123456789ab";
function linked(bindings, { host = "1.2.3.4", review = "pending", key = "NEWTHING|SN-NEW-01|1.0.0" } = {}) {
  const db = bindings.DB.db;
  db.prepare(`INSERT INTO readouts (id, key, stage, sender, fingerprint, bytes, object, received, offer)
    VALUES (?, ?, 'first', 's1', 'o', 10, 'readouts/x.json', '2026-11-09T10:00:00Z', 'o')`).run(READOUT, key);
  db.prepare("INSERT INTO readout_networks (id, network) VALUES (?, 'n1')").run(READOUT);
  db.prepare(`INSERT INTO offer_links (readout, sha, path, cloud, scheme, host, port, review)
    VALUES (?, ?, '/ota/s61/user_ota.rbl', 'ewelink', 'http', ?, 8088, ?)`).run(READOUT, SHA, host, review);
}
const sectionOf = (html, id) => html.split(`id="${id}">`)[1].split("</section>")[0];
const copyValues = (html) => [...html.matchAll(/<input id="(copy-\d+)" type="text" readonly value="([^"]*)"/g)]
  .map((match) => match[2]);

test("links to review are listed with their device, networks, first bytes and a copy field each", async () => {
  const bindings = env();
  linked(bindings, { key: 'NEW<b>|SN"01|1.0' });
  bindings.DB.db.prepare(`INSERT INTO link_peeks (cloud, link, day, outcome, status, size, server, location, kind, names, hex)
    VALUES ('ewelink', 'http://1.2.3.4:8088/ota/s61/user_ota.rbl', '2026-11-09', 'answered', 206, 2048,
      'eu-otadl.coolkit.cc:8088', NULL, 'sonoff-wrapper', 'FWSW-<i>', '0301<script>')`).run();
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  const review = sectionOf(html, "review");
  assert.match(review, /NEW&lt;b&gt; · SN&quot;01 · 1\.0/);
  assert.ok(!review.includes("<b>") && !review.includes("<i>") && !review.includes("<script>"));
  assert.match(review, /answered · 206 · from eu-otadl\.coolkit\.cc:8088 · 2\.0 KB in all · sonoff-wrapper · FWSW-&lt;i&gt;/);
  assert.match(review, /<code class="peek">0301&lt;script&gt;<\/code>/);
  assert.deepEqual(copyValues(review), ["http://1.2.3.4:8088/ota/s61/user_ota.rbl",
    ...["eu-otadl.coolkit.cc", "us-otadl.coolkit.cc", "as-otadl.coolkit.cc", "cn-otadl.coolkit.cn"]
      .map((name) => `http://${name}:8088/ota/s61/user_ota.rbl?deviceid=0&amp;ts=1&amp;sign=0`)]);
  assert.match(review, /<button type="button" data-copy="copy-1">Copy<\/button>/);
  assert.match(review, /name="action" value="approve"><button>Approve<\/button>/);
  assert.match(review, /name="action" value="reject"><button class="danger">Reject<\/button>/);
  // Nothing of a link in an href, a src or a script: only in fields' values.
  assert.ok(![...html.matchAll(/(?:href|src|action)="([^"]*)"/g)].some((match) => /coolkit|1\.2\.3\.4|ota\//.test(match[1])));
  assert.match(html, /<script src="\/assets\/copy\.js" defer><\/script>/);
  assert.ok(!admin.COPY_SCRIPT.includes("innerHTML") && !admin.COPY_SCRIPT.includes("href"));
  // Every link, copyable as the API fetches it.
  assert.deepEqual(copyValues(sectionOf(html, "links")), ["http://1.2.3.4:8088/ota/s61/user_ota.rbl"],
    "pending: as sent");
});

test("links to review not kept, the queue full, are counted over the days the counts are kept", async () => {
  const bindings = env();
  linked(bindings);
  const before = sectionOf(await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys), "review");
  assert.ok(!before.includes("not kept"), "none: nothing said");
  assert.match(before, new RegExp(`${PENDING_LINKS_MOST} wait at most`));
  for (const [day, count] of [["2026-11-08", 2], ["2026-11-09", 3]]) {
    bindings.DB.db.prepare("INSERT INTO counters (name, day, count) VALUES (?, ?, ?)").run(linksNotKeptOn(day), day, count);
  }
  const review = sectionOf(await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys), "review");
  assert.match(review, new RegExp(`5 not kept in the last ${COUNTER_DAYS} days: the\\s+queue was full, `
    + `or their sender had sent ${PENDING_LINKS_PER_SENDER} new ones that day\\.`));
});

test("a link a test makes from markup-looking parts is escaped wherever it is shown", async () => {
  const bindings = env();
  linked(bindings, { host: '"><script>x</script>', review: "approved" });
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.ok(!html.includes("<script>x"));
  assert.match(sectionOf(html, "review"), /Approved, not fetched yet\./);
  assert.match(sectionOf(html, "review"), /value="approve"><button>Try again<\/button>/);
  assert.ok(sectionOf(html, "links").includes('value="http://&quot;&gt;&lt;script&gt;x&lt;/script&gt;:8088/ota/s61/user_ota.rbl?deviceid=0&amp;ts=1&amp;sign=0"'));
});

test("every link the API took is listed with its fetched form to copy", async () => {
  const bindings = env();
  linked(bindings);
  bindings.DB.db.prepare(`INSERT INTO offer_links (readout, sha, path, cloud) VALUES (?, ?, ?, 'ewelink')`)
    .run(READOUT, "b".repeat(64), `/ota/rom/${"0".repeat(32)}/user1.bin`);
  const html = await admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
  assert.ok(copyValues(sectionOf(html, "links"))
    .includes(`http://eu-otadl.coolkit.cc:8088/ota/rom/${"0".repeat(32)}/user1.bin?deviceid=0&amp;ts=1&amp;sign=0`));
});

test("a link is approved, its file fetched, or rejected only from the view, by the signed-in", async () => {
  const objects = new Map();
  const bucket = { head: async (key) => (objects.has(key) ? {} : null), put: async (key, body) => objects.set(key, body),
    delete: async (key) => objects.delete(key) };
  const file = Buffer.from("the file");
  const bindings = env({ READOUTS: bucket });
  bindings.DB.db.prepare(`INSERT INTO readouts (id, key, stage, sender, fingerprint, bytes, object, received, offer)
    VALUES (?, 'K', 'first', 's1', 'o', 10, 'readouts/x.json', '2026-11-09T10:00:00Z', 'o')`).run(READOUT);
  const sha = createHash("sha256").update(file).digest("hex");
  bindings.DB.db.prepare(`INSERT INTO offer_links (readout, sha, path, cloud, scheme, host, port, review)
    VALUES (?, ?, '/x.bin', 'ewelink', 'https', 'a.coolkit.cc', NULL, 'pending')`).run(READOUT, sha);
  const asked = [];
  const files = async (url) => {
    asked.push(String(url));
    return new Response(file);
  };
  const post = async (fields, headers = { Origin: admin.ORIGIN, "Sec-Fetch-Site": "same-origin" }, jwt = true) =>
    admin.handle(new Request("https://admin.firmwareliberator.com/links", { method: "POST",
      body: new URLSearchParams(fields), headers: { ...(jwt ? { "Cf-Access-Jwt-Assertion": await token() } : {}),
        ...headers } }), bindings, NOW, keys, files);
  const review = () => rows(bindings.DB, "SELECT review FROM offer_links")[0].review;
  const fields = { readout: READOUT, sha, action: "approve" };
  assert.equal((await post(fields, undefined, false)).status, 403, "not signed in");
  assert.equal((await post(fields, { Origin: "https://evil.example" })).status, 403);
  assert.equal((await post(fields, { Origin: admin.ORIGIN, "Sec-Fetch-Site": "cross-site" })).status, 403);
  for (const bad of [{ ...fields, action: "fetch" }, { ...fields, sha: "x" }, { ...fields, readout: "r1'--" }]) {
    assert.equal((await post(bad)).status, 400, JSON.stringify(bad));
  }
  assert.deepEqual([review(), asked], ["pending", []], "nothing approved, nothing fetched");
  const done = await post(fields);
  assert.deepEqual([done.status, done.headers.get("Location")], [303, "/#review"]);
  assert.deepEqual([review(), asked], ["approved", ["https://a.coolkit.cc/x.bin?deviceid=0&ts=1&sign=0"]]);
  assert.ok(objects.has(`readouts/firmware/${sha}`));
  bindings.DB.db.prepare("UPDATE offer_links SET review = 'pending'").run();
  await post({ ...fields, action: "reject" });
  assert.equal(review(), "rejected");
  assert.equal(asked.length, 1, "rejecting fetches nothing");
});

test("the copy button's script is served from the view, to the signed-in alone", async () => {
  const script = async (jwt) => admin.handle(new Request("https://admin.firmwareliberator.com/assets/copy.js",
    { headers: jwt ? { "Cf-Access-Jwt-Assertion": jwt } : {} }), env(), NOW, keys);
  const served = await script(await token());
  assert.equal(served.headers.get("Content-Type"), "text/javascript; charset=utf-8");
  assert.equal(await served.text(), admin.COPY_SCRIPT);
  assert.match(admin.COPY_SCRIPT, /navigator\.clipboard\.writeText\(field\.value\)/);
  assert.equal((await script(null)).status, 403);
});
