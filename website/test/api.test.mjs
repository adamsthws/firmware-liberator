// What api/index.js, the project's API Worker, takes and turns away.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import worker, * as readout from "../api/index.js";

const NOW = new Date("2026-11-07T12:00:00Z");

function tar(files) {
  const blocks = [];
  for (const [name, text] of Object.entries(files)) {
    const data = Buffer.from(text);
    const header = Buffer.alloc(512);
    header.write(name, 0);
    header.write(data.length.toString(8).padStart(11, "0"), 124);
    blocks.push(header, data, Buffer.alloc((512 - (data.length % 512)) % 512));
  }
  return Buffer.concat([...blocks, Buffer.alloc(1024)]);
}

const MANIFEST = { format: 1, product: "NEWTHING", stock_model: "SN-NEW-01", version: "1.0.0", tool: "2.4.0",
  read: true };
const readoutOf = (manifest = MANIFEST, extra = {}) =>
  tar({ "manifest.json": JSON.stringify(manifest), "flash.bin.xz": "\xfd7zXZ", ...extra });

// The tool's work (readout.proof in tools/autoflash/readout.py), made easy for a test.
// Easier work than a real readout's, so the tests are quick (TEST_PROOF_BITS).
const BITS = 8;

function proofFor(body, bits = BITS, time = NOW.getTime() / 1000) {
  const digest = createHash("sha256").update(body).digest("hex");
  for (let nonce = 0; ; nonce += 1) {
    const hash = createHash("sha256").update(`${time}:${digest}:${nonce}`).digest();
    const zero = hash.readUInt32BE(0) >>> (32 - bits) === 0 && bits <= 32;
    if (zero) return `${time}:${nonce}`;
  }
}

function bucket() {
  const objects = new Map();
  return {
    objects,
    head: async (key) => (objects.has(key) ? { key } : null),
    list: async ({ prefix }) => ({
      objects: [...objects].filter(([key]) => key.startsWith(prefix)).map(([key, item]) => ({ key, size: item.size })),
    }),
    get: async (key) => (objects.has(key) ? { text: async () => objects.get(key).text } : null),
    put: async (key, body, options) => objects.set(key, {
      size: typeof body === "string" ? body.length : body.byteLength, options,
      text: typeof body === "string" ? body : "",
    }),
  };
}

// The readouts kept, leaving out the marks of what is held and the day's count.
const tars = (bindings) => [...bindings.READOUTS.objects.keys()].filter((key) => key.endsWith(".tar"));

function env({ allowed = true, enough = "" } = {}) {
  return { READOUTS: bucket(), READOUT_LIMIT: { limit: async () => ({ success: allowed }) }, ENOUGH: enough,
    TEST_PROOF_BITS: String(BITS) };
}

function post(body, headers = {}) {
  return new Request("https://api.firmwareliberator.com/readout", {
    method: "POST",
    body,
    headers: { "Content-Type": "application/x-tar", "Content-Length": String(body.byteLength),
      "CF-Connecting-IP": "192.0.2.9", "X-Proof": proofFor(body), ...headers },
  });
}

const send = (request, bindings) => readout.readout(request, bindings, NOW);

test("a readout with its work done is kept, under a reference it is given back", async () => {
  const bindings = env();
  const response = await send(post(readoutOf()), bindings);
  assert.equal(response.status, 201);
  const { id } = await response.json();
  assert.match(id, /^2026-11-07-[0-9a-f]{12}$/);
  const [key] = tars(bindings);
  const item = bindings.READOUTS.objects.get(key);
  assert.match(key, /^readouts\/2026-11-07\/[0-9a-f]{24}\.tar$/);
  assert.equal(item.options.customMetadata.product, "NEWTHING");
  assert.ok(!JSON.stringify(item.options).includes("192.0.2.9"), "the sender's address is kept");
});

test("the same one again is the one already kept", async () => {
  const bindings = env();
  const body = readoutOf();
  const first = await (await send(post(body), bindings)).json();
  const again = await send(post(body), bindings);
  assert.equal(again.status, 200);
  assert.deepEqual(await again.json(), first);
  assert.equal(tars(bindings).length, 1);
});

test("without its work, old work, or another body's, nothing is kept", async () => {
  const body = readoutOf();
  for (const proof of ["", "1:2:3", `${NOW.getTime() / 1000 - 3600}:0`, proofFor(readoutOf({ ...MANIFEST, x: 1 }))]) {
    const bindings = env();
    assert.equal((await send(post(body, { "X-Proof": proof }), bindings)).status, 403, proof);
    assert.equal(bindings.READOUTS.objects.size, 0);
  }
  const stale = proofFor(body, BITS, NOW.getTime() / 1000 - readout.PROOF_AGE - 60);
  assert.equal((await send(post(body, { "X-Proof": stale }), env())).status, 403);
});

test("one proof keeps one readout: another device's needs work of its own", async () => {
  // So a run reading out many devices cannot spread one proof over them all.
  const bindings = env();
  const first = readoutOf();
  const proof = proofFor(first);
  assert.equal((await send(post(first, { "X-Proof": proof }), bindings)).status, 201);
  const second = readoutOf({ ...MANIFEST, product: "OTHERTHING" });
  assert.equal((await send(post(second, { "X-Proof": proof }), bindings)).status, 403);
  assert.equal((await send(post(second), bindings)).status, 201);
  assert.equal(tars(bindings).length, 2);
});

test("anything but a readout the tool makes is turned away", async () => {
  const cases = [
    [new Request("https://api.firmwareliberator.com/readout"), 405],
    [post(readoutOf(), { "Content-Type": "application/octet-stream" }), 415],
    [post(Buffer.from("not a tar")), 400],
    [post(tar({ "flash.bin.xz": "x" })), 400],
    [post(readoutOf({ format: 2, product: "NEWTHING" })), 400],
    [post(readoutOf(), { "Content-Length": String(readout.MAX_BYTES + 1) }), 413],
  ];
  for (const [request, status] of cases) {
    const bindings = env();
    assert.equal((await send(request, bindings)).status, status);
    assert.equal(bindings.READOUTS.objects.size, 0);
  }
});

test("too many from one sender wait", async () => {
  const bindings = env({ allowed: false });
  assert.equal((await send(post(readoutOf()), bindings)).status, 429);
  assert.equal(bindings.READOUTS.objects.size, 0);
});

const dayCount = (bindings, day, count, bytes) => bindings.READOUTS.objects.set(`readouts/${day}/_readouts.json`,
  { size: 1, text: JSON.stringify({ count, bytes }) });

test("a day's limit, by count and by size, is never passed", async () => {
  const bindings = env();
  dayCount(bindings, "2026-11-07", readout.DAILY_COUNT, 1);
  assert.equal((await send(post(readoutOf()), bindings)).status, 503);
  const full = env();
  dayCount(full, "2026-11-07", 1, readout.DAILY_BYTES);
  assert.equal((await send(post(readoutOf()), full)).status, 503);
  const yesterday = env();
  dayCount(yesterday, "2026-11-06", readout.DAILY_COUNT, readout.DAILY_BYTES);
  assert.equal((await send(post(readoutOf()), yesterday)).status, 201);
});

test("the day's count is kept as readouts are, not listed", async () => {
  const bindings = { ...env(), READOUTS: { ...bucket(), list: async () => assert.fail("listed the day's readouts") } };
  for (const version of ["1.0.0", "1.0.1"]) {
    assert.equal((await send(post(readoutOf({ ...MANIFEST, version })), bindings)).status, 201);
  }
  const count = JSON.parse(bindings.READOUTS.objects.get("readouts/2026-11-07/_readouts.json").text);
  assert.equal(count.count, 2);
  assert.ok(count.bytes > 0);
});

test("a readout adds something or is thanked and not kept", async () => {
  const bindings = env();
  const kept = async (manifest) => (await send(post(readoutOf(manifest)), bindings)).status === 201;
  const first = { ...MANIFEST, read: false };
  assert.ok(await kept(first), "the first of its model and version");
  assert.ok(!(await kept({ ...first, tool: "2.4.1" })), "another first stage of it adds nothing");
  assert.ok(await kept({ ...first, version: "1.1.0" }), "another version does");
  assert.ok(await kept(MANIFEST), "a whole one, where only a first stage is held, does");
  assert.ok(!(await kept({ ...MANIFEST, tool: "2.4.1" })), "a second whole one adds nothing");
  assert.ok(!(await kept({ ...first, version: "1.0.0", tool: "2.4.2" })), "nor a first stage once a whole one is in");
  assert.ok(await kept({ ...MANIFEST, stock_model: "SN-NEW-02" }), "another firmware model does");
  const response = await send(post(readoutOf({ ...MANIFEST, tool: "2.4.3" })), bindings);
  assert.deepEqual(await response.json(), { enough: true });
});

test("what is held is marked where the lifecycle rule removes it with the readouts", async () => {
  const bindings = env();
  const offered = [{ file: "offered/user1.bin", version: "1.1.0", sha256: "a".repeat(64) },
    { file: null, version: "1.1.0", sha256: "b".repeat(64), held: true }];
  assert.equal((await send(post(readoutOf({ ...MANIFEST, offered })), bindings)).status, 201);
  const keys = [...bindings.READOUTS.objects.keys()];
  assert.ok(keys.includes("readouts/held/NEWTHING/SN-NEW-01/1.0.0/whole"));
  assert.ok(keys.includes(`readouts/firmware/${"a".repeat(64)}`));
  assert.ok(!keys.includes(`readouts/firmware/${"b".repeat(64)}`), "a file left out is marked by the readout that held it");
  assert.ok(keys.every((key) => key.startsWith("readouts/")));
});

test("a device enough readouts of are in is thanked, not kept", async () => {
  const bindings = env({ enough: "OTHER, newthing" });
  const response = await send(post(readoutOf()), bindings);
  assert.deepEqual([response.status, await response.json()], [200, { enough: true }]);
  assert.equal(bindings.READOUTS.objects.size, 0);
});

test("what it may keep stays within R2's free 10 GB", () => {
  const free = 10 * 1024 ** 3;
  assert.ok(readout.DAILY_BYTES * readout.RETENTION_DAYS + readout.KEPT_BYTES <= free);
  const settings = readFileSync(new URL("../../tools/repo/cloudflare_settings.py", import.meta.url), "utf8");
  assert.match(settings, new RegExp(`READOUTS_KEPT_DAYS = ${readout.RETENTION_DAYS}\\b`));
});

test("without TEST_PROOF_BITS the full work is asked", async () => {
  const body = readoutOf();
  const bindings = env();
  delete bindings.TEST_PROOF_BITS;
  assert.equal((await send(post(body), bindings)).status, 403);
  const config = readFileSync(new URL("../api/wrangler.jsonc", import.meta.url), "utf8");
  assert.doesNotMatch(config, /TEST_PROOF_BITS/);
});

test("the tool does the same work the Worker asks", () => {
  const tool = readFileSync(new URL("../../tools/autoflash/readout.py", import.meta.url), "utf8");
  assert.match(tool, new RegExp(`^PROOF_BITS = ${readout.PROOF_BITS}\\b`, "m"));
  assert.match(tool, new RegExp(`^LIGHT_PROOF_BITS = ${readout.LIGHT_PROOF_BITS}\\b`, "m"));
  assert.match(tool, /UPLOAD_URL = "https:\/\/api\.firmwareliberator\.com\/readout"/);
});

test("every other address is not found, and the site is not this Worker", async () => {
  const response = await worker.fetch(new Request("https://api.firmwareliberator.com/"), env());
  assert.equal(response.status, 404);
  const site = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8");
  assert.doesNotMatch(site, /readout|api\//i);
});

const ASKED = { format: 1, product: "NEWTHING", model: "SN-NEW-01", version: "1.0.0", stage: "first", firmware: [] };

function question(asked = ASKED, headers = {}, body = Buffer.from(JSON.stringify(asked)), path = "/wanted") {
  return new Request(`https://api.firmwareliberator.com${path}`, {
    method: "POST",
    body,
    headers: { "Content-Type": "application/json", "Content-Length": String(body.byteLength),
      "X-Proof": proofFor(body), ...headers },
  });
}

const ask = async (asked, bindings, now = NOW) => {
  const body = Buffer.from(JSON.stringify(asked));
  const proof = proofFor(body, BITS, Math.floor(now.getTime() / 1000));   // made as it is asked
  return (await readout.wanted(question(asked, { "X-Proof": proof }, body), bindings, now)).json();
};

test("asked first, a device enough readouts of are in is not wanted, any other is", async () => {
  const bindings = env({ enough: "S60TPG, basicr4" });
  for (const [product, wanted] of [["BASICR4", false], ["s60tpg", false], ["NEWTHING", true]]) {
    const answer = await ask({ ...ASKED, product }, bindings);
    assert.equal(answer.wanted, wanted, product);
    if (!wanted) assert.equal(answer.why, "enough");
  }
  assert.equal(bindings.READOUTS.objects.size, 0);
});

test("asked first, a readout that would add nothing is not wanted", async () => {
  const bindings = env();
  await send(post(readoutOf({ ...MANIFEST, read: false })), bindings);
  assert.deepEqual(await ask(ASKED, bindings), { wanted: false, why: "held", held: [] });
  assert.equal((await ask({ ...ASKED, version: "2.0.0" }, bindings)).wanted, true);
  assert.equal((await ask({ ...ASKED, stage: "whole" }, bindings)).wanted, true);
  await send(post(readoutOf(MANIFEST)), bindings);
  assert.equal((await ask({ ...ASKED, stage: "whole" }, bindings)).why, "held");
});

test("asked first, the files held already are named, wanted or not", async () => {
  const bindings = env();
  const offered = [{ file: "offered/user1.bin", version: "1.1.0", sha256: "a".repeat(64) }];
  await send(post(readoutOf({ ...MANIFEST, product: "OTHERTHING", offered })), bindings);
  const answer = await ask({ ...ASKED, firmware: ["a".repeat(64), "c".repeat(64)] }, bindings);
  assert.deepEqual(answer, { wanted: true, held: ["a".repeat(64)] });
  bindings.READOUTS.objects.set(`kept/firmware/${"c".repeat(64)}`, { size: 1 });
  assert.deepEqual((await ask({ ...ASKED, firmware: ["c".repeat(64)] }, bindings)).held, ["c".repeat(64)]);
});

test("asked first, a full day says so, from its count read once a minute", async () => {
  const bindings = env();
  let reads = 0;
  const get = bindings.READOUTS.get;
  bindings.READOUTS.get = async (key) => { reads += 1; return get(key); };
  dayCount(bindings, "2026-11-07", readout.DAILY_COUNT, 1);
  const at = new Date("2026-11-07T12:00:00Z");
  assert.deepEqual(await ask({ ...ASKED, product: "FULL1" }, bindings, at), { wanted: false, why: "full", held: [] });
  await ask({ ...ASKED, product: "FULL1" }, bindings, new Date(at.getTime() + 30_000));
  assert.equal(reads, 1, "read again within the minute");
  await ask({ ...ASKED, product: "FULL1" }, bindings, new Date(at.getTime() + 61_000));
  assert.equal(reads, 2);
});

test("each question needs work of its own, lighter than a readout's and newer", async () => {
  const body = Buffer.from(JSON.stringify(ASKED));
  const other = Buffer.from(JSON.stringify({ ...ASKED, product: "OTHERTHING" }));
  const minuteAgo = proofFor(body, BITS, NOW.getTime() / 1000 - readout.LIGHT_PROOF_AGE - 5);
  for (const proof of ["", "1:2:3", proofFor(other), minuteAgo]) {
    assert.equal((await readout.wanted(question(ASKED, { "X-Proof": proof }, body), env(), NOW)).status, 403, proof);
  }
  assert.ok(readout.LIGHT_PROOF_BITS < readout.PROOF_BITS);
  assert.ok(readout.LIGHT_PROOF_AGE < readout.PROOF_AGE);
});

test("a question is held to no readout limit, so a batch's leave its readouts theirs", async () => {
  const bindings = { ...env(), READOUT_LIMIT: { limit: async () => assert.fail("asked the readout limiter") } };
  assert.equal((await readout.wanted(question(), bindings, NOW)).status, 200);
});

test("anything but the tool's question is turned away", async () => {
  const bindings = env();
  const json = (value) => Buffer.from(JSON.stringify(value));
  const badly = [
    [new Request("https://api.firmwareliberator.com/wanted"), 405],
    [question(ASKED, { "Content-Type": "text/plain" }), 415],
    [question(ASKED, {}, json({ ...ASKED, product: "" })), 400],
    [question(ASKED, {}, json({ ...ASKED, stage: "some" })), 400],
    [question(ASKED, {}, json({ ...ASKED, version: undefined })), 400],
    [question(ASKED, {}, json({ ...ASKED, firmware: ["not a hash"] })), 400],
    [question(ASKED, {}, json({ ...ASKED, firmware: Array(readout.MAX_FIRMWARE + 1).fill("a".repeat(64)) })), 400],
    [question(ASKED, {}, json({ ...ASKED, format: 2 })), 400],
    [question(ASKED, {}, Buffer.from("not json")), 400],
    [question(ASKED, {}, Buffer.alloc(readout.QUESTION_MAX_BYTES + 1, 32)), 413],
  ];
  for (const [request, status] of badly) {
    assert.equal((await readout.wanted(request, bindings, NOW)).status, status);
  }
});

test("the Worker sends /wanted and /notify to their own", async () => {
  for (const [path, value, status] of [["/wanted", ASKED, 200],
    ["/notify", { format: 1, email: "me@example.com", devices: [{ product: "NEWTHING", model: "SN-NEW-01" }] }, 201]]) {
    const body = Buffer.from(JSON.stringify(value));
    const now = proofFor(body, BITS, Math.floor(Date.now() / 1000));   // fetch() checks it against the clock
    const response = await worker.fetch(question(value, { "X-Proof": now }, body, path), env());
    assert.equal(response.status, status, path);
  }
});

const NOTIFY = { format: 1, email: " Me@Example.com ", devices: [
  { product: "NEWTHING", model: "SN-NEW-01" }, { product: "newthing", model: "SN-NEW-02" },
  { product: "BASICR4", model: "-" }] };
const tell = (value, bindings, now = NOW) => readout.notify(question(value, {}, undefined, "/notify"), bindings, now);

test("an address is kept once a product, with its models, to be told of a change", async () => {
  const bindings = env({ enough: "NEWTHING" });   // wanted or not, an address is kept
  const response = await tell(NOTIFY, bindings);
  assert.equal(response.status, 201);
  const kept = [...bindings.READOUTS.objects].filter(([key]) => key.startsWith("notify/"));
  assert.deepEqual(kept.map(([key]) => key.split("/")[1]).sort(), ["BASICR4", "NEWTHING"]);
  const [, item] = kept.find(([key]) => key.startsWith("notify/NEWTHING/"));
  const value = JSON.parse(item.text);
  assert.deepEqual([value.email, value.product, value.models], ["Me@Example.com", "NEWTHING", ["SN-NEW-01", "SN-NEW-02"]]);
  const again = env();
  await tell({ ...NOTIFY, email: "me@example.com" }, again);
  assert.equal(kept.find(([key]) => key.startsWith("notify/NEWTHING/"))[0],
    [...again.READOUTS.objects.keys()].find((key) => key.startsWith("notify/NEWTHING/")), "one per address, any case");
});

test("an address is never under readouts/, which the lifecycle rule empties", async () => {
  const bindings = env();
  await tell(NOTIFY, bindings);
  for (const [key, item] of bindings.READOUTS.objects) {
    if (key.startsWith("readouts/")) assert.ok(!item.text.includes("xample"), key);
  }
});

test("a day's addresses are limited too", async () => {
  const bindings = env();
  bindings.READOUTS.objects.set("readouts/2026-11-07/_notify.json",
    { size: 1, text: JSON.stringify({ count: readout.NOTIFY_DAILY, bytes: 0 }) });
  assert.equal((await tell(NOTIFY, bindings)).status, 503);
});

test("anything but the tool's request to be told is turned away", async () => {
  for (const value of [{ ...NOTIFY, email: "not an email" }, { ...NOTIFY, devices: [] },
    { ...NOTIFY, devices: [{ model: "x" }] }, { ...NOTIFY, devices: Array(readout.MAX_DEVICES + 1).fill(NOTIFY.devices[0]) },
    { ...NOTIFY, format: 2 }]) {
    const bindings = env();
    assert.equal((await tell(value, bindings)).status, 400, JSON.stringify(value).slice(0, 80));
    assert.equal(bindings.READOUTS.objects.size, 0);
  }
});
