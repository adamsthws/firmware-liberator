// api.firmwareliberator.com: the project's API (tools/autoflash/readout.py,
// .github/readouts.md). A Worker of its own, apart from the website's, so
// nothing sent here can touch the site. Six paths: four a POST from the tool
// - /reports (reports.js, which says how it is guarded), and three for
// readouts - and /confirm and /forget, which a person opens from an email.
// Every one answers 503 while INGEST is not "on". A sender is counted by its
// address, an IPv6 one by its /48 (sender.js).
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
//   when a device's status changes, whether or not a readout was wanted. The
//   address is sent a link to confirm it, and nothing is kept until it is.
// - /confirm: that link; /forget: the link to stop, one an address. A GET of
//   either shows a page asking first; only the POST it sends acts, so a mail
//   scanner following one does nothing.
// - Readouts not taken as right are held to PENDING_BYTES, and deleted with
//   unconfirmed addresses by the nightly job after their days (pending.js).
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
//   minute (LIGHT_PROOF_AGE), and taken once: each request is worked for
//   afresh. Text a sender gives is taken only if it is printable ASCII.
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
export const MAX_DEVICES = 20;
// Sign-ups to be told of a change one sender may ask for a day, and one
// address may be sent a confirmation for: so nobody can have many strangers
// emailed, or one stranger many times.
export const NOTIFY_SENDER_DAILY = 5;
// Where the confirmation an address is sent comes from: on the domain the
// Email Service sends for (.github/readouts.md says how it was set up).
export const NOTIFY_FROM = "updates@firmwareliberator.com";
export const CONFIRM_URL = "https://api.firmwareliberator.com/confirm";
// An address not confirmed this many days after its link was sent is sent it
// once more (remind, run hourly): a confirmation is easily missed, and once is
// all, so a stranger's address given is written to twice at most.
export const REMIND_AFTER_DAYS = 1;
// Reminders sent a run at most; any more go the next hour.
export const REMIND_EACH_RUN = 100;
// wrangler.jsonc's cron for the nightly job; its other, hourly, sends the
// reminders alone.
export const NIGHTLY_CRON = "17 3 * * *";
// The most of the store that readouts not yet taken as right may take: so
// made-up ones, kept until the nightly job deletes them (pending.js), can
// never fill it.
export const PENDING_BYTES = 1024 * 1024 * 1024;
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
import { PENDING, SIGNUP_DAYS } from "./pending.js";
import { SUPPRESSED, sendMail } from "./mail.js";
import { senderAddress } from "./sender.js";
import { bodySubject, hex, proven, sha256, work } from "./proof-of-work.js";
import { nightly } from "./nightly.js";
import { countWanted, REPORTS_DAILY, reports } from "./reports.js";

export { work };

// Each path, and only these: tools/repo/cloudflare_settings.py's firewall rule lets
// through a POST to these alone.
export const ROUTES = { "/readout": readout, "/wanted": wanted, "/notify": notify, "/reports": reports,
  "/confirm": confirm, "/forget": forget };

export default {
  async fetch(request, env) {
    const route = ROUTES[new URL(request.url).pathname];
    if (!route) {
      return answer(404, { error: "readouts are sent to /readout, by the tool" });
    }
    // The one switch for every path: "on" takes requests; anything else, as
    // the website workflow can deploy with, answers 503 for the tool to try
    // again later.
    if (env.INGEST !== "on") {
      return answer(503, { error: "not taking requests for now: try again later" }, { "Retry-After": "3600" });
    }
    if (!(await ready(env.DB))) {
      return answer(503, { error: "the API is being updated: try again later" }, { "Retry-After": "300" });
    }
    return route(request, env);
  },
  // The nightly job (nightly.js) then the reminders (remind), or, at the
  // hourly cron, the reminders alone: at the times wrangler.jsonc's crons give.
  async scheduled(controller, env, context) {
    // The limits the maintainer is told were reached (alerts.js).
    const limits = { DAILY_COUNT, DAILY_BYTES, TOTAL_BYTES, NOTIFY_DAILY, REPORTS_DAILY, from: NOTIFY_FROM };
    const now = new Date(controller.scheduledTime);
    if (controller.cron !== NIGHTLY_CRON) {
      context.waitUntil(remind(env, now).then((reminded) => ({ reminded })));
      return;
    }
    context.waitUntil(nightly(env, now, limits).then(async (done) => ({ ...done, reminded: await remind(env, now) })));
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

// Text from a sender: printable ASCII alone, so nothing kept can carry a
// terminal's escape sequences to whoever reads it (tools/repo/readouts.py).
const PRINTABLE = /^[\x20-\x7e]+$/;
const text = (value, most = 64) => typeof value === "string" && value.length > 0 && value.length <= most
  && PRINTABLE.test(value);
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

// A sender's address, as senders are counted: IPv6 by its /48 (sender.js).
const addressOf = senderAddress;
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
  const agreed = (await env.DB.prepare(
    `UPDATE readouts SET review = 'quorum' WHERE key = ?1 AND stage = ?2 AND review = 'pending'
     AND fingerprint = ?3 AND (SELECT count(*) FROM readouts WHERE key = ?1 AND stage = ?2
       AND fingerprint = ?3 AND review != 'rejected') >= ?4 RETURNING bytes`,
  ).bind(row.key, row.stage, row.fingerprint, PER_KEY).all()).results;
  // Taken as right: no longer held to the pending readouts' room.
  const bytes = agreed.reduce((total, each) => total + each.bytes, 0);
  if (bytes) {
    await release(env.DB, PENDING, bytes, agreed.length);
  }
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
  const subject = await bodySubject(body);
  const proof = String(request.headers.get("X-Proof") ?? "");
  if (!(await proven(proof, subject, now.getTime() / 1000, bits, LIGHT_PROOF_AGE))) {
    return answer(403, { error: `the ${what}'s proof of work does not check out` });
  }
  // Each proof once: within its minute, one sent again is not taken.
  const day = now.toISOString().slice(0, 10);
  const used = hex(await sha256(new TextEncoder().encode(`${proof}|${subject}`))).slice(0, 32);
  if (!(await reserve(env.DB, `proof:${used}`, day, { most: 1 }))) {
    return answer(403, { error: `the ${what}'s proof of work was used already` });
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
  const { success } = await env.READOUT_LIMIT.limit({ key: addressOf(request) });
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
  if (!manifest || !text(manifest.product)) {
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
  // Pending until three agree or it is approved, and held to their room.
  if (!(await take(PENDING, null, { mostBytes: PENDING_BYTES }, body.byteLength))) {
    await giveBack();
    return answer(503, { error: "too many readouts are waiting for review: try again another time" },
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
    // Nothing of it left behind: a mark left would say a file is held that
    // is not, and nothing would ever delete it.
    const { results: marks } = await env.DB.prepare("SELECT sha FROM readout_firmware WHERE readout = ?").bind(id).all();
    for (const mark of marks) {
      await env.READOUTS.delete(firmwareKey(mark.sha));
    }
    await env.READOUTS.delete(key);
    await env.DB.prepare("DELETE FROM readout_firmware WHERE readout = ?").bind(id).run();
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
      tool: String(manifest.tool || "").replace(/[^\x20-\x7e]/g, "").slice(0, 16),
      product: manifest.product.slice(0, 40),
      contact: String(Boolean(manifest.contact)),
      read: String(manifest.read === true),
    },
  });
  // Each file it holds marked, once - the first readout to hold one keeps the
  // mark - and listed by the readout, so deleting it deletes its marks too:
  // listed first, so a mark is never written that its readout does not list.
  for (const item of Array.isArray(manifest.offered) ? manifest.offered.slice(0, MAX_FIRMWARE) : []) {
    if (typeof item?.sha256 === "string" && /^[0-9a-f]{64}$/.test(item.sha256) && !item.held
        && !(await isHeld(env, firmwareKey(item.sha256)))) {
      await env.DB.prepare("INSERT OR IGNORE INTO readout_firmware (readout, sha) VALUES (?1, ?2)")
        .bind(id, item.sha256).run();
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
// changes: {"format": 1, "email", "devices": [{"product", "model"}]}. Not
// kept as given: the address is sent a link to confirm it (/confirm), and
// only once its owner follows it is it kept, under
// notify/<product>/<the address's SHA-256>.json, one a product and address,
// until removed (/forget, by the link each such email carries). Asked and
// never confirmed, it is deleted after SIGNUP_DAYS (pending.js). Answers 202
// alike whether or not a confirmation was sent, so nobody learns from it
// whether an address was asked for before.
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
  if (!env.EMAIL) {
    return answer(503, { error: "not taking addresses yet: try again another time" }, { "Retry-After": "86400" });
  }
  const day = now.toISOString().slice(0, 10);
  const sender = await keyedHash(env, `notify|${day}|${addressOf(request)}`);
  if (!sender) {
    return answer(503, { error: "the API is being set up: try again later" }, { "Retry-After": "3600" });
  }
  if (!(await reserve(env.DB, `notify-sender:${sender}:${day}`, day, { most: NOTIFY_SENDER_DAILY }))) {
    return answer(503, { error: `${NOTIFY_SENDER_DAILY} a day from one address: try again tomorrow` },
      { "Retry-After": "86400" });
  }
  const who = hex(await sha256(new TextEncoder().encode(email.toLowerCase())));
  // One confirmation an address a day, however many ask for it.
  if (!(await reserve(env.DB, `notify-to:${who}:${day}`, day, { most: 1 }))) {
    return answer(202, { confirming: true });
  }
  if (!(await reserve(env.DB, notifyOn(day), day, { most: NOTIFY_DAILY }))) {
    return answer(503, { error: "enough for today: try again tomorrow" }, { "Retry-After": "86400" });
  }
  const token = hex(crypto.getRandomValues(new Uint8Array(16)));
  const named = devices.map((device) => ({ product: device.product.toUpperCase(), model: device.model ?? "-" }));
  await env.DB.prepare("INSERT INTO confirmations (token, who, email, devices, day, sent) VALUES (?1, ?2, ?3, ?4, ?5, ?6)")
    .bind(token, who, email, JSON.stringify(named), day, now.toISOString()).run();
  const mailed = await sendMail(env, confirmation(email, named, token), day);
  if (!mailed.sent) {
    await env.DB.prepare("DELETE FROM confirmations WHERE token = ?").bind(token).run();
    await release(env.DB, notifyOn(day));
    // An address that bounced for good, or marked one of ours as spam, is
    // answered as any other, so nobody learns that of it; it is not tried
    // again today.
    if (mailed.code === SUPPRESSED) {
      return answer(202, { confirming: true });
    }
    await release(env.DB, `notify-to:${who}:${day}`);
    return answer(502, { error: "the confirmation could not be sent: try again later" });
  }
  return answer(202, { confirming: true });
}

// The email asking an address's owner to confirm it, or, given the day it was
// asked for, asking once more.
function confirmation(email, devices, token, given = null) {
  const reminder = given !== null;
  const products = [...new Set(devices.map((device) => device.product))];
  return {
    to: email,
    from: NOTIFY_FROM,
    subject: `${reminder ? "Reminder - confirm" : "Confirm"}: tell me when my device is supported`,
    text: [
      `Someone - we hope you - asked Firmware Liberator${reminder ? ` on ${given}` : ""} to email this address when the`,
      `support for these devices changes: ${products.join(", ")}.`,
      "",
      `To be told, confirm it here: ${CONFIRM_URL}?t=${token}`,
      "",
      reminder
        ? `This is the only reminder. If it was not you, do nothing: unless it is confirmed, the address is deleted`
        : `If it was not you, do nothing: unless it is confirmed, the address is deleted in ${SIGNUP_DAYS} days`,
      reminder ? `${SIGNUP_DAYS} days after it was given, and never written to again.` : "and never written to again.",
    ].join("\n"),
  };
}

// The hourly cron's part (scheduled): each address sent its link
// REMIND_AFTER_DAYS or more ago, still not confirmed, is sent it once more - once an
// address, however many times it was given, and none already confirmed
// through another link. Counted against NOTIFY_DAILY like any confirmation.
// How many it sent.
export async function remind(env, now = new Date()) {
  if (!env.EMAIL || !(await ready(env.DB))) {
    return 0;
  }
  const today = now.toISOString().slice(0, 10);
  const before = new Date(now.getTime() - REMIND_AFTER_DAYS * 24 * 60 * 60 * 1000).toISOString();
  const { results } = await env.DB.prepare(`SELECT confirmations.* FROM confirmations
    LEFT JOIN counters ON counters.name = 'notify-remind:' || confirmations.who AND counters.count > 0
    WHERE confirmations.sent <= ?1 AND counters.name IS NULL ORDER BY confirmations.sent LIMIT ?2`)
    .bind(before, REMIND_EACH_RUN).all();
  let sent = 0;
  for (const signup of results) {
    // The counter outlives the sign-up (pruning in nightly.js, SIGNUP_DAYS in
    // pending.js), so a run twice reminds nobody twice.
    if (await env.READOUTS.head(whoKey(signup.who))
        || !(await reserve(env.DB, `notify-remind:${signup.who}`, today, { most: 1 }))) {
      continue;
    }
    if (!(await reserve(env.DB, notifyOn(today), today, { most: NOTIFY_DAILY }))) {
      await release(env.DB, `notify-remind:${signup.who}`);
      break;
    }
    const mailed = await sendMail(env, confirmation(signup.email, JSON.parse(signup.devices), signup.token, signup.day),
      today);
    if (mailed.sent) {
      sent += 1;
      continue;
    }
    await release(env.DB, notifyOn(today));
    // Suppressed, it is not to be tried again; any other failure, next hour.
    if (mailed.code !== SUPPRESSED) {
      await release(env.DB, `notify-remind:${signup.who}`);
    }
  }
  return sent;
}

// The link in a confirmation (?t=<token>): a GET shows what it confirms and
// a button, and only the POST that button sends keeps the address, so a mail
// scanner following the link confirms nothing.
export async function confirm(request, env, now = new Date()) {
  let token;
  if (request.method === "GET") {
    token = new URL(request.url).searchParams.get("t") ?? "";
  } else if (request.method === "POST") {
    token = new URLSearchParams(await request.text()).get("t") ?? "";
  } else {
    return answer(405, { error: "opened from the link in an email" }, { Allow: "GET, POST" });
  }
  const signup = TOKEN.test(token)
    ? await env.DB.prepare("SELECT * FROM confirmations WHERE token = ?").bind(token).first() : null;
  if (!signup) {
    return page(404, "Nothing to confirm",
      `<p>This link is not one we know, or it has been used, or more than ${SIGNUP_DAYS} days have passed.</p>`);
  }
  const devices = JSON.parse(signup.devices);
  const products = [...new Set(devices.map((device) => device.product))];
  if (request.method === "GET") {
    return page(200, "Be told when your device is supported?",
      `<p>Firmware Liberator will email you when the support for these changes: ${escape(products.join(", "))}.
Every such email has a link to stop them.</p>
<form method="post" action="/confirm"><input type="hidden" name="t" value="${token}">
<button type="submit">Yes, email me</button></form>`);
  }
  await keepAddress(env, signup.email, signup.who, devices, now);
  await env.DB.prepare("DELETE FROM confirmations WHERE token = ?").bind(token).run();
  return page(200, "Confirmed", "<p>You will be emailed when your device's support changes. Thank you for helping.</p>");
}

// A confirmed address, kept for each product, with its one removal link.
async function keepAddress(env, email, who, devices, now) {
  const products = [...new Set(devices.map((device) => device.product))];
  // One token an address, kept as it was first made: every email sent it
  // carries the same link.
  const before = await indexOf(env, who);
  const token = TOKEN.test(before?.token ?? "") ? before.token : hex(crypto.getRandomValues(new Uint8Array(16)));
  const forget = `${FORGET_URL}?a=${who}&t=${token}`;
  await env.READOUTS.put(whoKey(who), JSON.stringify({ token,
    products: [...new Set([...(Array.isArray(before?.products) ? before.products : []), ...products])] }),
  { httpMetadata: { contentType: "application/json" } });
  for (const product of products) {
    const models = [...new Set(devices.filter((device) => device.product === product).map((device) => device.model))];
    await env.READOUTS.put(`notify/${part(product)}/${who}.json`,
      JSON.stringify({ email, product, models, since: now.toISOString(), forget }),
      // Its address, models and removal link in its metadata too: tools/repo/readouts.py notify lists
      // them without fetching each one.
      { httpMetadata: { contentType: "application/json" },
        customMetadata: { product, email, models: models.join(",").slice(0, 512), forget } });
  }
}

// Text put in a page's HTML, from wherever it came.
const escape = (value) => String(value).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

function page(status, title, body) {
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
