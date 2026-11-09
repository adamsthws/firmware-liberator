// api.firmwareliberator.com: the project's API (tools/autoflash/readout.py,
// .github/readouts.md). A Worker of its own, apart from the website's, so
// nothing sent here can touch the site. Five paths: four a POST from the tool
// - /reports (reports.js, which says how it is guarded), and three for
// readouts - and /forget, the one a person opens:
// - /wanted: whether a readout of a device would add anything, asked before
//   it is made. Its product model, firmware model and version, the stage it
//   would be (a first stage, or a whole memory), and the firmware eWeLink
//   offers it, by SHA-256: the answer says whether it is wanted, and which of
//   those files are held already, so they are left out.
// - /readout: a readout, made with its owner's consent. Kept only if it adds
//   something: fewer than PER_KEY readouts of that model and version at that
//   stage are held (a whole one taking a first stage's place too), and its
//   sender has had fewer than SENDER_DAILY kept today.
// - /notify: an owner's email address and their devices' models, to be told
//   when a device's status changes, whether or not a readout was wanted, with
//   one removal link an address.
// - /forget: that link. A GET shows a page asking to confirm; only the POST
//   it sends removes the address, so a mail scanner following it cannot.
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
//   request, by its owner through /forget. No address or other detail of a
//   sender is kept: a sender's day count is named by a keyed hash of its
//   address (SENDER_KEY, a Worker secret), deleted with the day's counts.

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
// Readouts kept of each product, firmware model and version, each from a
// different sender: three, so one made up stands out against the two that
// agree, and none is taken as right until that many do (readouts table).
export const PER_KEY = 3;
// Readouts kept a day from one sender's address (SENDER_DAILY), counted under
// a keyed hash of it (SENDER_KEY, a Worker secret), never the address itself.
export const SENDER_DAILY = 20;

import { answer } from "./answer.js";
import { read, release, reserve } from "./counters.js";
import { ready } from "./database.js";
import { bodySubject, hex, proven, sha256, work } from "./proof-of-work.js";
import { nightly } from "./nightly.js";
import { countWanted, reports } from "./reports.js";

export { work };

// Each path, and only these: tools/repo/cloudflare_settings.py's firewall rule lets
// through a POST to these alone.
export const ROUTES = { "/readout": readout, "/wanted": wanted, "/notify": notify, "/reports": reports,
  "/forget": forget };

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

// Where an offered file a readout holds is marked.
const firmwareKey = (sha) => `readouts/firmware/${sha}`;

const isHeld = async (env, key) => (await env.READOUTS.head(key)) !== null;

// A readout's key: its product, firmware model and version.
const keyOf = (product, model, version) => `${product.toUpperCase()}|${model}|${version}`;

// The readouts table's rows that hold a place: all but those rejected. A
// whole one takes a first stage's place too, as it holds all one does.
const HOLDING = "review != 'rejected' AND (?2 = 'first' OR stage = 'whole')";

// Whether a readout at `stage` would add anything for `sender`: a place is
// left, and that sender has none of the key already - so a sender with many
// of one device sends one, and three agreeing come from three senders.
async function adds(env, key, stage, sender) {
  const row = await env.DB.prepare(
    `SELECT (SELECT count(*) FROM readouts WHERE key = ?1 AND ${HOLDING}) AS held,
            (SELECT count(*) FROM readouts WHERE key = ?1 AND sender = ?3) AS theirs`,
  ).bind(key, stage, sender ?? "").first();
  return row.held < PER_KEY && row.theirs === 0;
}

// A keyed hash (SENDER_KEY) of `what`, 32 hex: null where the Worker has none.
const senderKeys = new Map();
async function keyedHash(env, what) {
  if (!env.SENDER_KEY) {
    return null;
  }
  if (!senderKeys.has(env.SENDER_KEY)) {
    senderKeys.set(env.SENDER_KEY, await crypto.subtle.importKey("raw", new TextEncoder().encode(env.SENDER_KEY),
      { name: "HMAC", hash: "SHA-256" }, false, ["sign"]));
  }
  const mac = await crypto.subtle.sign("HMAC", senderKeys.get(env.SENDER_KEY), new TextEncoder().encode(what));
  return hex(new Uint8Array(mac)).slice(0, 32);
}

const addressOf = (request) => request.headers.get("CF-Connecting-IP") || "unknown";
// The sender of a readout of `key`, as its row names it: one hash a key, so
// nothing ties one sender's readouts of different keys together.
const senderOf = (env, request, key) => keyedHash(env, `${key}|${addressOf(request)}`);

// JSON with its keys in order, so the same fields always hash alike.
const canonical = (value) => Array.isArray(value) ? `[${value.map(canonical).join(",")}]`
  : value && typeof value === "object"
    ? `{${Object.keys(value).sort().map((name) => `${JSON.stringify(name)}:${canonical(value[name])}`).join(",")}}`
    : JSON.stringify(value ?? null);

// What every genuine readout of a key at its stage shares, hashed: for a
// whole one, its firmware as the tool read it before anything was taken out
// (manifest.firmware: the partition table, each app's build); for a first
// stage, the files eWeLink offers it. Not the owner's settings, which differ.
async function fingerprintOf(manifest, stage) {
  const offered = (Array.isArray(manifest.offered) ? manifest.offered : [])
    .map((item) => String(item?.sha256 ?? "")).filter((sha) => /^[0-9a-f]{64}$/.test(sha)).sort();
  const shared = stage === "whole" ? { firmware: manifest.firmware ?? null, layout: manifest.layout ?? null }
    : { offered };
  return hex(await sha256(new TextEncoder().encode(canonical(shared))));
}

// A readout's row, if a place is left and its sender has none of the key:
// in one statement, so two sent at once cannot both take the last place.
// Then every pending row of its key and stage marked agreed (quorum) once
// PER_KEY of them hold the same fingerprint.
async function hold(env, row) {
  const taken = await env.DB.prepare(
    `INSERT INTO readouts (id, key, stage, sender, fingerprint, bytes, object, received)
     SELECT ?4, ?1, ?2, ?3, ?5, ?6, ?7, ?8
     WHERE (SELECT count(*) FROM readouts WHERE key = ?1 AND ${HOLDING}) < ?9
     ON CONFLICT (key, sender) DO NOTHING RETURNING id`,
  ).bind(row.key, row.stage, row.sender, row.id, row.fingerprint, row.bytes, row.object, row.received, PER_KEY)
    .first();
  if (!taken) {
    return false;
  }
  await env.DB.prepare(
    `UPDATE readouts SET review = 'quorum' WHERE key = ?1 AND stage = ?2 AND review = 'pending'
     AND fingerprint = ?3 AND (SELECT count(*) FROM readouts WHERE key = ?1 AND stage = ?2
       AND fingerprint = ?3 AND review != 'rejected') >= ?4`,
  ).bind(row.key, row.stage, row.fingerprint, PER_KEY).run();
  return true;
}

// The day's count of one sender's readouts, under a keyed hash of their
// address and the day: null where the Worker has no SENDER_KEY.
async function senderOn(env, request, day) {
  const hash = await keyedHash(env, `${day}|${addressOf(request)}`);
  return hash ? `sender:${hash}:${day}` : null;
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
// {"wanted": bool, "why": "enough"|"held"|"full"|"store-full"|"limit" when not,
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
  const key = keyOf(asked.product, asked.model, asked.version);
  if (!(await adds(env, key, asked.stage, await senderOf(env, request, key)))) {
    return no("held");
  }
  const day = now.toISOString().slice(0, 10);
  const full = await fullness(env, day);
  if (full) {
    return no(full);
  }
  const sender = await senderOn(env, request, day);
  if (sender && (await read(env.DB, sender)).count >= SENDER_DAILY) {
    return no("limit");
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
  const sender = await senderOn(env, request, day);
  if (!sender) {
    return answer(503, { error: "the API is being set up: try again later" }, { "Retry-After": "3600" });
  }
  // Each place taken in turn, and given back in turn if a later one is not.
  const taken = [];
  const giveBack = async () => {
    for (const [name, bytes] of taken.reverse()) {
      await release(env.DB, name, bytes);
    }
  };
  const take = async (name, period, limits, bytes = 0) => {
    const got = await reserve(env.DB, name, period, { ...limits, bytes });
    if (got) {
      taken.push([name, bytes]);
    }
    return got;
  };
  // A readout of a key its sender has sent already, or with no place left,
  // is thanked and not kept - before it takes any of its sender's day.
  const readoutKey = keyOf(manifest.product, model, version);
  const from = await senderOf(env, request, readoutKey);
  if (!(await adds(env, readoutKey, stage, from))) {
    return answer(200, { enough: true });
  }
  if (!(await take(sender, day, { most: SENDER_DAILY }))) {
    await giveBack();
    return answer(503, { error: `${SENDER_DAILY} readouts a day from one address: try again tomorrow`, limit: true },
      { "Retry-After": "86400" });
  }
  if (!(await take(readoutsOn(day), day, { most: DAILY_COUNT, mostBytes: DAILY_BYTES }, body.byteLength))) {
    await giveBack();
    return answer(503, { error: "enough readouts for today: try again tomorrow" }, { "Retry-After": "86400" });
  }
  if (!(await take(READOUTS_EVER, null, { mostBytes: TOTAL_BYTES }, body.byteLength))) {
    await giveBack();
    return answer(503, { error: "the store of readouts is full: try again another time" },
      { "Retry-After": "604800" });
  }
  const row = { key: readoutKey, stage, sender: from, id, fingerprint: await fingerprintOf(manifest, stage),
    bytes: body.byteLength, object: key, received: now.toISOString() };
  if (!(await hold(env, row))) {
    await giveBack();   // its place taken meanwhile, by another sent at once
    return answer(200, { enough: true });
  }
  try {
    await keep(env, key, body, manifest, id);
  } catch (error) {
    await env.DB.prepare("DELETE FROM readouts WHERE id = ?").bind(id).run();
    await giveBack();
    throw error;
  }
  return answer(201, { id });
}

// A readout, with the marks of what it holds.
async function keep(env, key, body, manifest, id) {
  await env.READOUTS.put(key, body, {
    httpMetadata: { contentType: "application/x-tar" },
    customMetadata: {
      tool: String(manifest.tool || "").slice(0, 16),
      product: manifest.product.slice(0, 40),
      contact: String(Boolean(manifest.contact)),
      read: String(manifest.read === true),
    },
  });
  for (const item of Array.isArray(manifest.offered) ? manifest.offered.slice(0, MAX_FIRMWARE) : []) {
    if (typeof item?.sha256 === "string" && /^[0-9a-f]{64}$/.test(item.sha256) && !item.held) {
      await env.READOUTS.put(firmwareKey(item.sha256), id, { customMetadata: { readout: key } });
    }
  }
}

const EMAIL = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const WHO = /^[0-9a-f]{64}$/;
const TOKEN = /^[0-9a-f]{32}$/;
export const FORGET_URL = "https://api.firmwareliberator.com/forget";

// One address's index: its removal token and the products it was given
// for, so /forget removes them all with no listing.
const whoKey = (who) => `notify/_who/${who}.json`;
async function indexOf(env, who) {
  const found = await env.READOUTS.get(whoKey(who));
  if (!found) {
    return null;
  }
  try {
    return JSON.parse(await found.text());
  } catch {
    return null;
  }
}

// An owner's email address, to be told when any of their devices' status
// changes: {"format": 1, "email", "devices": [{"product", "model"}]}. Kept
// under notify/<product>/<the address's SHA-256>.json, one a product and
// address, until it is written to or removed (/forget, by the link each such
// email carries: forgetLink, in each one's metadata as `forget`).
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
  // One token an address, kept as it was first made: every email sent it
  // carries the same link.
  const before = await indexOf(env, who);
  const token = TOKEN.test(before?.token ?? "") ? before.token : hex(crypto.getRandomValues(new Uint8Array(16)));
  const forget = `${FORGET_URL}?a=${who}&t=${token}`;
  await env.READOUTS.put(whoKey(who), JSON.stringify({ token,
    products: [...new Set([...(Array.isArray(before?.products) ? before.products : []), ...products])] }),
  { httpMetadata: { contentType: "application/json" } });
  for (const product of products) {
    const models = devices.filter((device) => device.product.toUpperCase() === product)
      .map((device) => device.model).filter(Boolean);
    await env.READOUTS.put(`notify/${part(product)}/${who}.json`,
      JSON.stringify({ email, product, models: [...new Set(models)], since: now.toISOString(), forget }),
      // Its address, models and removal link in its metadata too: tools/repo/readouts.py notify lists
      // them without fetching each one.
      { httpMetadata: { contentType: "application/json" },
        customMetadata: { product, email, models: [...new Set(models)].join(",").slice(0, 512), forget } });
  }
  return answer(201, { products });
}

function page(status, title, body) {
  const escape = (value) => String(value).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
  return new Response(`<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex">
<title>${escape(title)}</title><style>body{font:16px/1.5 system-ui,sans-serif;max-width:32rem;margin:3rem auto;padding:0 1rem}
button{font:inherit;padding:.5rem 1rem}</style></head><body><h1>${escape(title)}</h1>${body}</body></html>`,
  { status, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'",
    "Referrer-Policy": "no-referrer" } });
}

// Whether `token` is the one `who`'s index holds, compared in fixed time.
function sameToken(index, token) {
  const kept = String(index?.token ?? "");
  if (!TOKEN.test(kept) || !TOKEN.test(token)) {
    return false;
  }
  let differ = 0;
  for (let i = 0; i < kept.length; i += 1) {
    differ |= kept.charCodeAt(i) ^ token.charCodeAt(i);
  }
  return differ === 0;
}

// The link in each email to an address given (?a=<its SHA-256>&t=<token>):
// a GET shows what it would do and a button, and only the POST that button
// sends removes the address - a mail scanner following the link removes
// nothing. Every product it was given for goes, and its index.
export async function forget(request, env) {
  let who, token;
  if (request.method === "GET") {
    const url = new URL(request.url);
    [who, token] = [url.searchParams.get("a") ?? "", url.searchParams.get("t") ?? ""];
  } else if (request.method === "POST") {
    const form = new URLSearchParams(await request.text());
    [who, token] = [form.get("a") ?? "", form.get("t") ?? ""];
  } else {
    return answer(405, { error: "opened from the link in an email" }, { Allow: "GET, POST" });
  }
  const index = WHO.test(who) ? await indexOf(env, who) : null;
  if (!index || !sameToken(index, token)) {
    return page(404, "Nothing to remove",
      "<p>This link is not one we know, or the address it was for has been removed already.</p>");
  }
  if (request.method === "GET") {
    return page(200, "Stop these emails?",
      `<p>Your email address will be removed from Firmware Liberator's list, for every device you gave it
for. You will not be emailed about them again.</p>
<form method="post" action="/forget"><input type="hidden" name="a" value="${who}">
<input type="hidden" name="t" value="${token}"><button type="submit">Remove my address</button></form>`);
  }
  for (const product of Array.isArray(index.products) ? index.products.slice(0, MAX_DEVICES) : []) {
    await env.READOUTS.delete(`notify/${part(String(product))}/${who}.json`);
  }
  await env.READOUTS.delete(whoKey(who));
  return page(200, "Removed", "<p>Your email address is off the list. Thank you for helping.</p>");
}
