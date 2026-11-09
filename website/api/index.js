// api.firmwareliberator.com: the project's API (tools/autoflash/readout.py,
// .github/readouts.md). A Worker of its own, apart from the website's, so
// nothing sent here can touch the site. Four paths, all POST from the tool:
// /reports (reports.js, which says how it is guarded), and three for readouts:
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
//   everyone, counted in the database (counters.js), where two at once can
//   never both pass the limit. /wanted reads that count too, and says so once
//   the day is full. NOTIFY_DAILY addresses a day may be given.
// - Until the database has every migration this code needs (database.js),
//   each path answers 503, so the tool tries again later.
// - Kept for good, with the marks of what is held (readouts/held/,
//   readouts/firmware/): a readout is kept only if it adds something, so the
//   store grows only with firmware not seen before, and an old one may prove
//   useful long after. TOTAL_BYTES of them at most, counted in the database
//   as the day's are, keeps the bucket within R2's free 10 GB, with room for
//   the addresses under notify/; past it, none is taken until some are
//   deleted by hand (.github/readouts.md).
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
export const TOTAL_BYTES = 8 * 1024 * 1024 * 1024;
export const FORMAT = 1;
export const LIGHT_PROOF_BITS = 20;
export const LIGHT_PROOF_AGE = 60;
export const QUESTION_MAX_BYTES = 4096;
export const NOTIFY_DAILY = 500;
export const MAX_FIRMWARE = 8;
export const MAX_DEVICES = 50;
export const STAGES = ["first", "whole"];

import { answer } from "./answer.js";
import { read, release, reserve } from "./counters.js";
import { ready } from "./database.js";
import { bodySubject, hex, proven, sha256, work } from "./proof-of-work.js";
import { nightly } from "./nightly.js";
import { countWanted, reports } from "./reports.js";

export { work };

const ROUTES = { "/readout": readout, "/wanted": wanted, "/notify": notify, "/reports": reports };

export default {
  async fetch(request, env) {
    const route = ROUTES[new URL(request.url).pathname];
    if (!route) {
      return answer(404, { error: "readouts are sent to /readout, by the tool" });
    }
    if (!(await ready(env.DB))) {
      return answer(503, { error: "the API is being updated: try again later" }, { "Retry-After": "300" });
    }
    return route(request, env);
  },
  // The nightly job (nightly.js), at the time wrangler.jsonc's cron gives.
  async scheduled(controller, env, context) {
    context.waitUntil(nightly(env, new Date(controller.scheduledTime)));
  },
};


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

// Where a readout of this model, version and stage is marked held, and an
// offered file it holds.
const heldKey = (product, model, version, stage) =>
  `readouts/held/${part(product.toUpperCase())}/${part(model)}/${part(version)}/${stage}`;
const firmwareKey = (sha) => `readouts/firmware/${sha}`;

const isHeld = async (env, key) => (await env.READOUTS.head(key)) !== null;

// Whether a readout at `stage` adds anything: none at that stage is held, and
// for a first stage, no whole one either, which holds all a first stage does.
async function adds(env, product, model, version, stage) {
  const stages = stage === "whole" ? ["whole"] : ["first", "whole"];
  for (const each of stages) {
    if (await isHeld(env, heldKey(product, model, version, each))) {
      return false;
    }
  }
  return true;
}

// The names of the counts (counters.js): the day's readouts kept, and
// addresses given, and the bytes of every readout ever kept.
const readoutsOn = (day) => `readouts:${day}`;
const notifyOn = (day) => `notify:${day}`;
const READOUTS_EVER = "readouts:all";

// Why no readout can be kept now, or null: "store-full", the store's limit
// reached, which waiting a day will not change, or "full", the day's.
const fullness = async (env, day) => {
  const [today, ever] = await Promise.all([read(env.DB, readoutsOn(day)), read(env.DB, READOUTS_EVER)]);
  if (ever.bytes >= TOTAL_BYTES) {
    return "store-full";
  }
  return today.count >= DAILY_COUNT || today.bytes >= DAILY_BYTES ? "full" : null;
};

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
  const bits = work.bits ?? LIGHT_PROOF_BITS;
  if (!(await proven(request.headers.get("X-Proof"), await bodySubject(body), now.getTime() / 1000, bits,
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
// {"wanted": bool, "why": "enough"|"held"|"full"|"store-full" when not,
// "held": [<those of the files kept already>]}.
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
  await countWanted(env.DB, asked.product, now.toISOString().slice(0, 10));
  const held = [];
  for (const sha of firmware) {
    if (await isHeld(env, firmwareKey(sha))) {
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
  const full = await fullness(env, now.toISOString().slice(0, 10));
  if (full) {
    return no(full);
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
  const digest = await bodySubject(body);
  const bits = work.bits ?? PROOF_BITS;
  if (!(await proven(request.headers.get("X-Proof"), digest, now.getTime() / 1000, bits, PROOF_AGE))) {
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
  if (!(await reserve(env.DB, readoutsOn(day), day,
    { bytes: body.byteLength, most: DAILY_COUNT, mostBytes: DAILY_BYTES }))) {
    return answer(503, { error: "enough readouts for today: try again tomorrow" }, { "Retry-After": "86400" });
  }
  if (!(await reserve(env.DB, READOUTS_EVER, null, { bytes: body.byteLength, mostBytes: TOTAL_BYTES }))) {
    await release(env.DB, readoutsOn(day), body.byteLength);
    return answer(503, { error: "the store of readouts is full: try again another time" },
      { "Retry-After": "604800" });
  }
  try {
    await keep(env, key, body, manifest, model, version, stage, id);
  } catch (error) {
    await release(env.DB, readoutsOn(day), body.byteLength);
    await release(env.DB, READOUTS_EVER, body.byteLength);
    throw error;
  }
  return answer(201, { id });
}

// A readout, with the marks of what it holds.
async function keep(env, key, body, manifest, model, version, stage, id) {
  await env.READOUTS.put(key, body, {
    httpMetadata: { contentType: "application/x-tar" },
    customMetadata: {
      tool: String(manifest.tool || "").slice(0, 16),
      product: manifest.product.slice(0, 40),
      contact: String(Boolean(manifest.contact)),
      read: String(manifest.read === true),
    },
  });
  await env.READOUTS.put(heldKey(manifest.product, model, version, stage), id, { customMetadata: { readout: key } });
  for (const item of Array.isArray(manifest.offered) ? manifest.offered.slice(0, MAX_FIRMWARE) : []) {
    if (typeof item?.sha256 === "string" && /^[0-9a-f]{64}$/.test(item.sha256) && !item.held) {
      await env.READOUTS.put(firmwareKey(item.sha256), id, { customMetadata: { readout: key } });
    }
  }
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
  if (!(await reserve(env.DB, notifyOn(day), day, { most: NOTIFY_DAILY }))) {
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
  return answer(201, { products });
}
