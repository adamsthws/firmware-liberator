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

const MANIFEST = { format: 1, product: "NEWTHING", tool: "2.4.0", read: true };
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
    put: async (key, body, options) => objects.set(key, { size: body.byteLength, options }),
  };
}

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
  const [[key, item]] = [...bindings.READOUTS.objects];
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
  assert.equal(bindings.READOUTS.objects.size, 1);
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

test("a day's limit, by count and by size, is never passed", async () => {
  const bindings = env();
  for (let index = 0; index < readout.DAILY_COUNT; index += 1) {
    bindings.READOUTS.objects.set(`readouts/2026-11-07/${index}.tar`, { size: 1 });
  }
  assert.equal((await send(post(readoutOf()), bindings)).status, 503);
  const full = env();
  full.READOUTS.objects.set("readouts/2026-11-07/big.tar", { size: readout.DAILY_BYTES });
  assert.equal((await send(post(readoutOf()), full)).status, 503);
  const yesterday = env();
  yesterday.READOUTS.objects.set("readouts/2026-11-06/big.tar", { size: readout.DAILY_BYTES });
  assert.equal((await send(post(readoutOf()), yesterday)).status, 201);
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
  assert.match(tool, new RegExp(`PROOF_BITS = ${readout.PROOF_BITS}\\b`));
  assert.match(tool, /UPLOAD_URL = "https:\/\/api\.firmwareliberator\.com\/readout"/);
});

test("every other address is not found, and the site is not this Worker", async () => {
  const response = await worker.fetch(new Request("https://api.firmwareliberator.com/"), env());
  assert.equal(response.status, 404);
  const site = readFileSync(new URL("../wrangler.jsonc", import.meta.url), "utf8");
  assert.doesNotMatch(site, /readout|api\//i);
});
