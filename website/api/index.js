// api.firmwareliberator.com: the project's API (tools/autoflash/readout.py,
// .github/readouts.md). A Worker of its own, apart from the website's, so
// nothing sent here can touch the site. Three paths, all POST from the tool:
// - /wanted: whether a readout of a device would add anything, asked before
//   it is made. Its product model, firmware model and version, the stage it
//   would be (a first stage, or a whole memory), and the firmware eWeLink
//   offers it, by SHA-256: the answer says whether it is wanted, and which of
//   those files are held already, so they are left out.
// - /readout: a readout, made with its owner's consent. Kept only if it adds
//   something: no readout of that model and version at that stage (or a whole
//   one, which holds a first stage's too) is held.
// - /notify: an owner's email address and their devices' models, to be told
//   when a device's status changes, whether or not a readout was wanted.
//
// The tool is open source, so it can hold no secret: what keeps this from being
// flooded is that each request costs its sender work, and that the bucket has
// hard limits it is never let past.
// - A proof of work: SHA-256 of "<time>:<the body's SHA-256>:<nonce>" starting
//   with PROOF_BITS zero bits, about 17 million tries (10 to 30 seconds in the
//   tool's Python, once a readout), made within PROOF_AGE of now and good for
//   that body alone. Hardware made for it does that in milliseconds, so the
//   daily limits below, not this, are what bound the bucket. /wanted and
//   /notify ask lighter work (LIGHT_PROOF_BITS, a second or two) good for a
//   minute (LIGHT_PROOF_AGE): each request is worked for afresh.
// - It must be a readout: a tar of at most MAX_BYTES, its first file a
//   manifest.json of the format the tool writes.
// - At most two readouts a minute from one address (READOUT_LIMIT, held by the
//   limiter, never stored), and DAILY_COUNT readouts and DAILY_BYTES a day from
//   everyone, counted in one small object a day (readouts/<day>/_count.json)
//   rather than by listing the day's readouts. /wanted reads it too, cached a
//   minute in the Worker, and says so once the day is full. NOTIFY_DAILY
//   addresses a day may be given.
// - Kept RETENTION_DAYS: the bucket's lifecycle rule (tools/repo/
//   cloudflare_settings.py) deletes everything under readouts/, with the marks
//   of what is held (readouts/held/, readouts/firmware/) and the day counts.
//   One worth keeping is moved under kept/ by hand, with its marks
//   (kept/held/..., kept/firmware/...). So the bucket stays under R2's free
//   10 GB: DAILY_BYTES * RETENTION_DAYS, plus KEPT_BYTES for kept/.
// - A device enough readouts of are in, by eWeLink's product model in ENOUGH
//   (a comma-separated variable in wrangler.jsonc), is not wanted at all.
// - The same readout sent again the same day is the one already stored.
// - The addresses under notify/ have no expiry: they are kept until the
//   device's status changes and they are written to, or until removed on
//   request. No address or other detail of a sender is kept.

export const MAX_BYTES = 24 * 1024 * 1024;
export const PROOF_BITS = 24;
export const PROOF_AGE = 15 * 60;
export const DAILY_COUNT = 100;
export const DAILY_BYTES = 250 * 1024 * 1024;
export const RETENTION_DAYS = 30;
export const KEPT_BYTES = 2 * 1024 * 1024 * 1024;
export const FORMAT = 1;
export const LIGHT_PROOF_BITS = 20;
export const LIGHT_PROOF_AGE = 60;
export const QUESTION_MAX_BYTES = 4096;
export const NOTIFY_DAILY = 500;
export const MAX_FIRMWARE = 8;
export const MAX_DEVICES = 50;
export const STAGES = ["first", "whole"];

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/readout") {
      return readout(request, env);
    }
    if (path === "/wanted") {
      return wanted(request, env);
    }
    if (path === "/notify") {
      return notify(request, env);
    }
    return answer(404, { error: "readouts are sent to /readout, by the tool" });
  },
};

function answer(status, body, headers = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", "Cache-Control": "no-store", ...headers },
  });
}

async function sha256(data) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", data));
}

const hex = (bytes) => [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");

function zeroBits(bytes) {
  let bits = 0;
  for (const byte of bytes) {
    if (byte === 0) {
      bits += 8;
      continue;
    }
    return bits + Math.clz32(byte) - 24;
  }
  return bits;
}

// Whether "<time>:<nonce>" proves the work for a body of this hash, now.
// `bits` is PROOF_BITS but in the tests, which ask for less, and /wanted's
// lighter work; `age` how long ago it may have been made.
export async function proven(proof, digest, now = Date.now() / 1000, bits = PROOF_BITS, age = PROOF_AGE) {
  const [time, nonce, extra] = String(proof || "").split(":");
  if (extra !== undefined || !/^\d{1,12}$/.test(time || "") || !/^\d{1,20}$/.test(nonce || "")) {
    return false;
  }
  if (Math.abs(now - Number(time)) > age) {
    return false;
  }
  return zeroBits(await sha256(new TextEncoder().encode(`${time}:${digest}:${nonce}`))) >= bits;
}

// The manifest a readout starts with, or null: the first file of its tar,
// which is not compressed (the files after it are, each on its own).
export function manifestOf(body) {
  try {
    const head = new Uint8Array(body, 0, Math.min(body.byteLength, 512 + 65536));
    const text = (start, end) => new TextDecoder().decode(head.slice(start, end));
    const name = text(0, 100).replace(/\0.*$/s, "");
    const size = parseInt(text(124, 136), 8);
    if (name !== "manifest.json" || !(size > 0 && size <= 65536) || head.length < 512 + size) {
      return null;
    }
    const manifest = JSON.parse(text(512, 512 + size));
    if (manifest?.format !== FORMAT || typeof manifest.product !== "string") {
      return null;
    }
    return manifest;
  } catch {
    return null;
  }
}

// The product models enough readouts of are in (ENOUGH), upper-case.
function enoughOf(env) {
  return String(env.ENOUGH || "").split(",").map((model) => model.trim().toUpperCase()).filter(Boolean);
}

const text = (value, most = 64) => typeof value === "string" && value.length > 0 && value.length <= most;
const part = (value) => encodeURIComponent(String(value));

// Where a readout of this model, version and stage is marked held: under
// readouts/ (gone with the readouts, after RETENTION_DAYS) and kept/.
const heldKeys = (product, model, version, stage) =>
  ["readouts", "kept"].map((top) =>
    `${top}/held/${part(product.toUpperCase())}/${part(model)}/${part(version)}/${stage}`);
const firmwareKeys = (sha) => ["readouts", "kept"].map((top) => `${top}/firmware/${sha}`);

async function anyHeld(env, keys) {
  for (const key of keys) {
    if (await env.READOUTS.head(key)) {
      return true;
    }
  }
  return false;
}

// Whether a readout at `stage` adds anything: none at that stage is held, and
// for a first stage, no whole one either, which holds all a first stage does.
async function adds(env, product, model, version, stage) {
  const stages = stage === "whole" ? ["whole"] : ["first", "whole"];
  for (const each of stages) {
    if (await anyHeld(env, heldKeys(product, model, version, each))) {
      return false;
    }
  }
  return true;
}

// The day's count: {count, bytes} of the readouts kept, or of the addresses
// given (`what`: "readouts" or "notify"), in one small object a day.
const countKey = (day, what) => `readouts/${day}/_${what}.json`;

async function counted(env, day, what) {
  const found = await env.READOUTS.get(countKey(day, what));
  if (!found) {
    return { count: 0, bytes: 0 };
  }
  try {
    const value = JSON.parse(await found.text());
    return { count: Number(value.count) || 0, bytes: Number(value.bytes) || 0 };
  } catch {
    return { count: 0, bytes: 0 };
  }
}

async function addCount(env, day, what, bytes) {
  const now = await counted(env, day, what);
  const next = { count: now.count + 1, bytes: now.bytes + bytes };
  await env.READOUTS.put(countKey(day, what), JSON.stringify(next),
    { httpMetadata: { contentType: "application/json" } });
  cacheOf(env).delete(`${day}/${what}`);
  return next;
}

// /wanted's reads of the day's count, a minute at a time in this Worker: a
// readout is turned away by its own fresh count once the day is full anyway.
// Keyed by the bucket's binding, which is one in a deployed Worker.
const caches = new WeakMap();
const cacheOf = (env) => caches.get(env.READOUTS) ?? caches.set(env.READOUTS, new Map()).get(env.READOUTS);
async function cachedCount(env, day, what, now) {
  const kept = cacheOf(env).get(`${day}/${what}`);
  if (kept && now - kept.at < 60_000) {
    return kept.value;
  }
  const value = await counted(env, day, what);
  cacheOf(env).set(`${day}/${what}`, { at: now, value });
  return value;
}

const full = (today, bytes = 0) => today.count >= DAILY_COUNT || today.bytes + bytes > DAILY_BYTES;

// A question of the tool's, its light work done: the parsed JSON, or a Response.
async function question(request, env, now, what) {
  if (request.method !== "POST") {
    return answer(405, { error: "asked by the tool, with POST" }, { Allow: "POST" });
  }
  if (request.headers.get("Content-Type") !== "application/json") {
    return answer(415, { error: `${what} is application/json` });
  }
  const declared = Number(request.headers.get("Content-Length"));
  if (!Number.isInteger(declared) || declared <= 0 || declared > QUESTION_MAX_BYTES) {
    return answer(413, { error: `${what} says its length, at most ${QUESTION_MAX_BYTES} bytes` });
  }
  const body = await request.arrayBuffer();
  if (body.byteLength !== declared) {
    return answer(400, { error: `that is not a whole ${what}` });
  }
  const bits = env.TEST_PROOF_BITS ? Number(env.TEST_PROOF_BITS) : LIGHT_PROOF_BITS;
  if (!(await proven(request.headers.get("X-Proof"), hex(await sha256(body)), now.getTime() / 1000, bits,
    LIGHT_PROOF_AGE))) {
    return answer(403, { error: `the ${what}'s proof of work does not check out` });
  }
  try {
    const asked = JSON.parse(new TextDecoder().decode(body));
    if (asked?.format === FORMAT) {
      return asked;
    }
  } catch {
    // below
  }
  return answer(400, { error: `that is not a ${what} this tool makes` });
}

// Whether a readout of a device would add anything, asked before it is made:
// {"format": 1, "product", "model", "version", "stage": "first"|"whole",
//  "firmware": [<SHA-256 of each file eWeLink offers it>]}. Answers
// {"wanted": bool, "why": "enough"|"held"|"full" when not, "held": [<those
// of the files kept already>]}.
export async function wanted(request, env, now = new Date()) {
  const asked = await question(request, env, now, "question");
  if (asked instanceof Response) {
    return asked;
  }
  const firmware = asked.firmware ?? [];
  if (!text(asked.product) || !text(asked.model) || !text(asked.version, 32) || !STAGES.includes(asked.stage)
      || !Array.isArray(firmware) || firmware.length > MAX_FIRMWARE
      || !firmware.every((sha) => typeof sha === "string" && /^[0-9a-f]{64}$/.test(sha))) {
    return answer(400, { error: "that is not a question this tool asks" });
  }
  const held = [];
  for (const sha of firmware) {
    if (await anyHeld(env, firmwareKeys(sha))) {
      held.push(sha);
    }
  }
  const no = (why) => answer(200, { wanted: false, why, held });
  if (enoughOf(env).includes(asked.product.toUpperCase())) {
    return no("enough");
  }
  if (!(await adds(env, asked.product, asked.model, asked.version, asked.stage))) {
    return no("held");
  }
  if (full(await cachedCount(env, now.toISOString().slice(0, 10), "readouts", now.getTime()))) {
    return no("full");
  }
  return answer(200, { wanted: true, held });
}

export async function readout(request, env, now = new Date()) {
  if (request.method !== "POST") {
    return answer(405, { error: "readouts are sent here by the tool, with POST" }, { Allow: "POST" });
  }
  if (request.headers.get("Content-Type") !== "application/x-tar") {
    return answer(415, { error: "a readout is application/x-tar" });
  }
  const declared = Number(request.headers.get("Content-Length"));
  if (!Number.isInteger(declared) || declared <= 0) {
    return answer(411, { error: "a readout says its length" });
  }
  if (declared > MAX_BYTES) {
    return answer(413, { error: `a readout is at most ${MAX_BYTES} bytes` });
  }
  const { success } = await env.READOUT_LIMIT.limit({ key: request.headers.get("CF-Connecting-IP") || "unknown" });
  if (!success) {
    return answer(429, { error: "too many readouts at once: try again in a minute" });
  }
  const body = await request.arrayBuffer();
  if (body.byteLength !== declared) {
    return answer(400, { error: "that is not a whole readout" });
  }
  const digest = hex(await sha256(body));
  const bits = env.TEST_PROOF_BITS ? Number(env.TEST_PROOF_BITS) : PROOF_BITS;
  if (!(await proven(request.headers.get("X-Proof"), digest, now.getTime() / 1000, bits))) {
    return answer(403, { error: "the readout's proof of work does not check out" });
  }
  const manifest = manifestOf(body);
  if (!manifest) {
    return answer(400, { error: "that is not a readout this tool makes" });
  }
  const day = now.toISOString().slice(0, 10);
  const id = `${day}-${digest.slice(0, 12)}`;
  const key = `readouts/${day}/${digest.slice(0, 24)}.tar`;
  if (await env.READOUTS.head(key)) {
    return answer(200, { id });
  }
  if (enoughOf(env).includes(manifest.product.toUpperCase())) {
    return answer(200, { enough: true });
  }
  const stage = manifest.read === true ? "whole" : "first";
  const model = text(manifest.stock_model) ? manifest.stock_model : "-";
  const version = text(manifest.version, 32) ? manifest.version : "-";
  if (!(await adds(env, manifest.product, model, version, stage))) {
    return answer(200, { enough: true });
  }
  if (full(await counted(env, day, "readouts"), body.byteLength)) {
    return answer(503, { error: "enough readouts for today: try again tomorrow" }, { "Retry-After": "86400" });
  }
  await env.READOUTS.put(key, body, {
    httpMetadata: { contentType: "application/x-tar" },
    customMetadata: {
      tool: String(manifest.tool || "").slice(0, 16),
      product: manifest.product.slice(0, 40),
      contact: String(Boolean(manifest.contact)),
      read: String(manifest.read === true),
    },
  });
  const [heldKey] = heldKeys(manifest.product, model, version, stage);
  await env.READOUTS.put(heldKey, id, { customMetadata: { readout: key } });
  for (const item of Array.isArray(manifest.offered) ? manifest.offered.slice(0, MAX_FIRMWARE) : []) {
    if (typeof item?.sha256 === "string" && /^[0-9a-f]{64}$/.test(item.sha256) && !item.held) {
      await env.READOUTS.put(firmwareKeys(item.sha256)[0], id, { customMetadata: { readout: key } });
    }
  }
  await addCount(env, day, "readouts", body.byteLength);
  return answer(201, { id });
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

// An owner's email address, to be told when any of their devices' status
// changes: {"format": 1, "email", "devices": [{"product", "model"}]}. Kept
// under notify/<product>/<the address's SHA-256>.json, one a model and
// address, until it is written to or removed on request.
export async function notify(request, env, now = new Date()) {
  const asked = await question(request, env, now, "request");
  if (asked instanceof Response) {
    return asked;
  }
  const devices = asked.devices;
  const email = typeof asked.email === "string" ? asked.email.trim() : "";
  if (!text(email, 254) || !EMAIL.test(email) || !Array.isArray(devices) || !devices.length
      || devices.length > MAX_DEVICES || !devices.every((device) => text(device?.product) && text(device?.model ?? "-"))) {
    return answer(400, { error: "that is not a request this tool makes" });
  }
  const day = now.toISOString().slice(0, 10);
  if ((await counted(env, day, "notify")).count >= NOTIFY_DAILY) {
    return answer(503, { error: "enough for today: try again tomorrow" }, { "Retry-After": "86400" });
  }
  const who = hex(await sha256(new TextEncoder().encode(email.toLowerCase())));
  const products = [...new Set(devices.map((device) => device.product.toUpperCase()))];
  for (const product of products) {
    const models = devices.filter((device) => device.product.toUpperCase() === product)
      .map((device) => device.model).filter(Boolean);
    await env.READOUTS.put(`notify/${part(product)}/${who}.json`,
      JSON.stringify({ email, product, models: [...new Set(models)], since: now.toISOString() }),
      // Its address and models in its metadata too: tools/repo/readouts.py notify lists them without
      // fetching each one.
      { httpMetadata: { contentType: "application/json" },
        customMetadata: { product, email, models: [...new Set(models)].join(",").slice(0, 512) } });
  }
  await addCount(env, day, "notify", 0);
  return answer(201, { products });
}
