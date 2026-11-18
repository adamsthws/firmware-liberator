// The files eWeLink offers a device that a first stage (/v1/offers) or a whole
// readout (/v1/readout, index.js) sent only links for, fetched by the API
// itself from the cloud that offers them, as the device would fetch them:
// - On first sight: each file a readout kept gives a link for, not held, is
//   fetched by that link after the answer (fetchNamed). A file fetched proves
//   itself: it must hash to the SHA-256 given, and the cloud's files are a
//   finite set, so however many offers are made up the store holds no more
//   than those files. A link that did not give its file is not tried again
//   for it on first sight (offer_fetch_failures), so the same made-up link
//   sent again costs nothing.
// - Once an offer is taken as right (index.js OFFER_AGREES), and each night
//   after (retryOffers, nightly.js), each file of it not held yet is tried by
//   the links given for it, those from the most networks first,
//   OFFER_FETCH_PATHS at most (fetchOffer): a link that gave another file
//   never again, one that gave nothing once a day. Agreement still says which
//   device an offer is of; a file is held as soon as it is fetched.
// - Kept as a readout's offered file is marked (readouts/firmware/<sha>, its
//   readout_firmware row listing it under the readout whose link fetched it,
//   its offer_links row its bytes), the mark holding the file itself and the
//   firmware names its wrapper gives (firmwareNames, as `names`, joined by
//   "+"), for review: a readout deleted takes it with it. Its bytes count
//   towards the store's limit (TOTAL_BYTES) and its readout's.
// - OFFER_FETCHES_DAILY fetches a day at most, of either kind. A file still
//   missing OFFER_FETCH_ALERT_DAYS after its offer was taken as right is told
//   to the maintainer (alerts.js): the sign to set OFFERED_SEND back to
//   "files" (wrangler.jsonc, .github/readouts.md).
import { release, reserve } from "./counters.js";
import { READOUTS_EVER } from "./pending.js";
import { hex, sha256 } from "./proof-of-work.js";

// Each cloud an offer may come from: the shape its links take, the part its
// readouts' key adds (index.js keyOf; none for eWeLink, so every key held
// from before, all eWeLink devices', stays as it is), and where a link's path
// is fetched from: `base`, the path, then `query`.
// - eWeLink: a link names its server by address, which a Worker cannot fetch
//   (Cloudflare's error 1003), but every region's server gives every link's
//   path by name; the device's id, time and signature are asked for and not
//   checked (docs/espressif-research.md, "Learning the firmware name").
export const CLOUDS = {
  ewelink: {
    link: /^\/ota\/rom\/[A-Za-z0-9]{32}\/[A-Za-z0-9._-]{1,64}$/,
    key: "",
    base: "http://eu-otadl.coolkit.cc:8088",
    query: "deviceid=0&ts=1&sign=0",
  },
};

// Fetches a day at most, across everyone, first sight's and agreement's:
// each file is fetched once, and each link that fails is not tried again on
// first sight, so this is room for a day's new releases many times over.
export const OFFER_FETCHES_DAILY = 50;
// First-sight fetches a day from one sender's offers and readouts (counted
// by a keyed hash of its address, as index.js SENDER_OFFERS_DAILY is): so one
// sender's made-up links can never use up the day's OFFER_FETCHES_DAILY
// before the real owners' files are tried. One past it is no failure: its
// offer's agreement, or the nightly retry, fetches it. Fetches for offers
// taken as right are not held to it.
export const OFFER_FIRST_SIGHT_PER_SENDER = 5;
// The most a file fetched may be: as tools/ewelink/offered_firmware.py's
// MAX_SIZE, more than any Sonoff update file.
export const OFFER_FETCH_MAX_BYTES = 8 * 1024 * 1024;
// Links tried for one file of an offer taken as right, each a different one
// given for it.
export const OFFER_FETCH_PATHS = 3;
// Offers tried again a night at most (retryOffers): with OFFER_FETCHES_DAILY,
// what holds the night's queries to a few dozen; any more the next night.
export const OFFER_RETRY_EACH_NIGHT = 5;
// Days after its offer was taken as right that a file still not fetched is
// told to the maintainer, and each as many days after.
export const OFFER_FETCH_ALERT_DAYS = 3;

// Sonoff's wrapper at the start of an ESP32's or a Beken's update file, as
// tools/ewelink/firmware_name.py reads it (docs/espressif-research.md,
// "Learning the firmware name"): byte 1 its record count, its 76-byte records
// from 0x18, each starting with a firmware name of up to 32 bytes. A Beken
// file has it after an outer header (FOTA_MAGIC), whose size its bytes 12 and
// 13 add to 16. An ESP8266 file has none: it starts with the chip's own image
// header (ea 04), and no record of it reads as a name.
const FOTA_MAGIC = 0x1beef11e;
const FOTA_HEADER_SIZE = 16;
const RECORDS_AT = 0x18;
const RECORD_SIZE = 76;
const NAME_SIZE = 32;
const A_NAME = /^[A-Z][A-Z0-9_]*(-[A-Z0-9_]+)*$/;

// The firmware names in a file's wrapper, in its order: none for a file
// without one. Kept with the file for review; nothing is refused on them.
export function firmwareNames(bytes) {
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const base = bytes.length >= FOTA_HEADER_SIZE && view.getUint32(0, true) === FOTA_MAGIC
    ? FOTA_HEADER_SIZE + view.getUint16(12, true) : 0;
  if (bytes.length < base + 2) {
    return [];
  }
  const names = [];
  for (let index = 0; index < bytes[base + 1]; index += 1) {
    const at = base + RECORDS_AT + index * RECORD_SIZE;
    const record = bytes.subarray(at, Math.min(at + NAME_SIZE, bytes.length));
    const end = record.indexOf(0);
    const name = String.fromCharCode(...record.subarray(0, end < 0 ? record.length : end));
    if (!A_NAME.test(name)) {
      break;
    }
    names.push(name);
  }
  return names;
}

// The day's count of files fetched (counters.js).
export const fetchesOn = (day) => `offer-fetches:${day}`;
// A sender's day count of first-sight fetches (counters.js), by the keyed
// hash index.js names it with.
export const firstSightOf = (hash, day) => `first-sight:${hash}:${day}`;
const firmwareKey = (sha) => `readouts/firmware/${sha}`;

// What `url` gave: {bytes}, or {why}: "none", nothing given, or "other",
// more than OFFER_FETCH_MAX_BYTES, which no file of the cloud's is.
async function download(fetcher, url) {
  let response;
  try {
    response = await fetcher(url);
  } catch {
    return { why: "none" };
  }
  if (!response.ok || !response.body) {
    await response.body?.cancel();
    return { why: "none" };
  }
  if (Number(response.headers.get("Content-Length")) > OFFER_FETCH_MAX_BYTES) {
    await response.body.cancel();
    return { why: "other" };
  }
  const chunks = [];
  let size = 0;
  try {
    const reader = response.body.getReader();
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      size += value.byteLength;
      if (size > OFFER_FETCH_MAX_BYTES) {
        await reader.cancel();
        return { why: "other" };
      }
      chunks.push(value);
    }
  } catch {
    return { why: "none" };
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, at);
    at += chunk.byteLength;
  }
  return { bytes };
}

// A link that did not give its file, noted (offer_fetch_failures).
const FAILED = `INSERT INTO offer_fetch_failures (sha, cloud, path, why, day) VALUES (?1, ?2, ?3, ?4, ?5)
  ON CONFLICT DO UPDATE SET why = excluded.why, day = excluded.day`;

// One link of a file tried: {sha, cloud, path, readout}, the readout it is
// kept under if it gives the file. "kept"; "held", kept meanwhile by another;
// "failed"; or "capped", where the day's fetches, the sender's (`sender`, a
// counter's name, on first sight), or the store ran out.
async function tryLink(env, link, day, { totalBytes, fetcher, sender = null }) {
  const cloud = Object.hasOwn(CLOUDS, link.cloud) ? CLOUDS[link.cloud] : null;
  if (!cloud || !cloud.link.test(link.path)) {
    return "failed";
  }
  if (sender && !(await reserve(env.DB, sender, day, { most: OFFER_FIRST_SIGHT_PER_SENDER }))) {
    return "capped";
  }
  if (!(await reserve(env.DB, fetchesOn(day), day, { most: OFFER_FETCHES_DAILY }))) {
    if (sender) {
      await release(env.DB, sender);
    }
    return "capped";
  }
  const { bytes, why } = await download(fetcher, `${cloud.base}${link.path}?${cloud.query}`);
  if (!bytes || hex(await sha256(bytes)) !== link.sha) {
    await env.DB.prepare(FAILED).bind(link.sha, link.cloud, link.path, why ?? "other", day).run();
    return "failed";
  }
  if (!(await reserve(env.DB, READOUTS_EVER, null, { by: 0, bytes: bytes.byteLength, mostBytes: totalBytes }))) {
    return "capped";
  }
  return (await keepFile(env, link, bytes)) ? "kept" : "held";
}

// Whether each outcome tryLink gives ends the trying of a file's links.
const FOUND = new Set(["kept", "held"]);

// Each file a readout `readout` kept gives a link for, not held, and whose
// link has not failed for it before, fetched by that link: first sight.
// `totalBytes`: the store's limit (index.js TOTAL_BYTES); `sender`, the name
// of the readout's sender's day count (firstSightOf). What it did:
// {fetched, missing, capped}.
export async function fetchNamed(env, readout, now, { totalBytes, sender, fetcher = fetch }) {
  const day = now.toISOString().slice(0, 10);
  const done = { fetched: [], missing: [], capped: false };
  const { results: links } = await env.DB.prepare(`SELECT sha, cloud, path, readout FROM offer_links
    WHERE readout = ?1 AND NOT EXISTS (SELECT 1 FROM offer_fetch_failures AS failed
      WHERE failed.sha = offer_links.sha AND failed.cloud = offer_links.cloud AND failed.path = offer_links.path)
    ORDER BY sha`).bind(readout).all();
  for (const link of links) {
    if ((await env.READOUTS.head(firmwareKey(link.sha))) !== null) {
      continue;
    }
    const outcome = await tryLink(env, link, day, { totalBytes, fetcher, sender });
    if (outcome === "capped") {
      done.capped = true;
      break;
    }
    if (outcome !== "held") {
      (outcome === "kept" ? done.fetched : done.missing).push(link.sha);
    }
  }
  return done;
}

// Each file of offer `offer` of `key` that links were given for and is not
// held, fetched and kept if it is what the offer agreed on. `totalBytes` as
// fetchNamed takes it. What it did: {fetched, missing, capped}.
export async function fetchOffer(env, key, offer, now, { totalBytes, fetcher = fetch }) {
  const day = now.toISOString().slice(0, 10);
  const done = { fetched: [], missing: [], capped: false };
  // The files links were given for, by readouts of it not rejected.
  const { results: files } = await env.DB.prepare(`SELECT DISTINCT offer_links.sha FROM offer_links
    JOIN readouts ON readouts.id = offer_links.readout
    WHERE readouts.key = ?1 AND readouts.offer = ?2 AND readouts.review != 'rejected'`).bind(key, offer).all();
  for (const { sha } of files) {
    if (done.capped || (await env.READOUTS.head(firmwareKey(sha))) !== null) {
      continue;
    }
    // The link most networks gave first: a file's link is the same for every
    // device, so made-up ones from one network come after the owners'. None
    // that gave another file, or gave nothing today.
    const { results: links } = await env.DB.prepare(`SELECT offer_links.sha, offer_links.cloud, offer_links.path,
      min(offer_links.readout) AS readout FROM offer_links JOIN readouts ON readouts.id = offer_links.readout
      LEFT JOIN readout_networks ON readout_networks.id = readouts.id
      WHERE readouts.key = ?1 AND readouts.offer = ?2 AND readouts.review != 'rejected' AND offer_links.sha = ?3
        AND NOT EXISTS (SELECT 1 FROM offer_fetch_failures AS failed WHERE failed.sha = offer_links.sha
          AND failed.cloud = offer_links.cloud AND failed.path = offer_links.path
          AND (failed.why = 'other' OR failed.day = ?5))
      GROUP BY offer_links.cloud, offer_links.path
      ORDER BY count(DISTINCT readout_networks.network) DESC, min(readouts.received) LIMIT ?4`)
      .bind(key, offer, sha, OFFER_FETCH_PATHS, day).all();
    let outcome = "failed";
    for (const link of links) {
      outcome = await tryLink(env, link, day, { totalBytes, fetcher });
      if (outcome === "capped") {
        done.capped = true;
      }
      if (outcome === "capped" || FOUND.has(outcome)) {
        break;
      }
    }
    if (outcome !== "held") {
      (outcome === "kept" ? done.fetched : done.missing).push(sha);
    }
  }
  return done;
}

// A file fetched, kept as its mark under the readout whose link fetched it,
// unless another holds it already: listed first, and only where none lists
// it, so a mark is never written that its readout does not list and two
// fetched at once are not both kept; its bytes added to the readout's, and to
// its link's, so deleting the readout gives them back. Whether it was kept:
// if not, the store's bytes reserved for it are given back.
async function keepFile(env, { readout, sha }, bytes) {
  const size = bytes.byteLength;
  const claimed = await env.DB.prepare(`INSERT INTO readout_firmware (readout, sha) SELECT ?1, ?2
    WHERE NOT EXISTS (SELECT 1 FROM readout_firmware WHERE sha = ?2) RETURNING sha`).bind(readout, sha).first();
  if (!claimed) {
    await release(env.DB, READOUTS_EVER, size, 0);
    return false;
  }
  const object = await env.DB.prepare("SELECT object FROM readouts WHERE id = ?").bind(readout).first("object");
  await env.DB.batch([
    env.DB.prepare("UPDATE readouts SET bytes = bytes + ?2 WHERE id = ?1").bind(readout, size),
    env.DB.prepare("UPDATE offer_links SET bytes = ?3 WHERE readout = ?1 AND sha = ?2").bind(readout, sha, size),
    env.DB.prepare("DELETE FROM offer_fetch_failures WHERE sha = ?").bind(sha),
  ]);
  const names = firmwareNames(bytes);
  try {
    await env.READOUTS.put(firmwareKey(sha), bytes, { customMetadata: { readout: object ?? "", fetched: "true",
      ...(names.length ? { names: names.join("+") } : {}) } });
  } catch (error) {
    await env.DB.batch([
      env.DB.prepare("DELETE FROM readout_firmware WHERE readout = ?1 AND sha = ?2").bind(readout, sha),
      env.DB.prepare("UPDATE readouts SET bytes = bytes - ?2 WHERE id = ?1").bind(readout, size),
      env.DB.prepare("UPDATE offer_links SET bytes = NULL WHERE readout = ?1 AND sha = ?2").bind(readout, sha),
    ]);
    await release(env.DB, READOUTS_EVER, size, 0);
    throw error;
  }
  return true;
}

// The offers taken as right with a file linked and not held: of first stages
// and whole readouts taken as right, agreed (quorum) or approved.
const UNFETCHED = `SELECT DISTINCT readouts.key, readouts.offer FROM offer_links
  JOIN readouts ON readouts.id = offer_links.readout
  WHERE readouts.review IN ('quorum', 'approved') AND offer_links.sha NOT IN (SELECT sha FROM readout_firmware)`;

// The nightly job's part (nightly.js): each offer taken as right whose files
// are not all held, tried again within the day's fetches, OFFER_RETRY_EACH_NIGHT
// at most. What it did: {offers, fetched, missing}.
export async function retryOffers(env, now, { totalBytes, fetcher = fetch }) {
  const { results } = await env.DB.prepare(`${UNFETCHED} LIMIT ?1`).bind(OFFER_RETRY_EACH_NIGHT).all();
  const tried = { offers: results.length, fetched: 0, missing: 0 };
  for (const { key, offer } of results) {
    const done = await fetchOffer(env, key, offer, now, { totalBytes, fetcher });
    tried.fetched += done.fetched.length;
    tried.missing += done.missing.length;
    if (done.capped) {
      break;
    }
  }
  return tried;
}

// The offers agreed (agreed_offers) with a file linked and still not held:
// each {key, offered, day, files}, the day it was taken as right and how many
// of its files are missing.
export async function unfetched(db) {
  const { results } = await db.prepare(`SELECT agreed_offers.key, agreed_offers.offered, agreed_offers.day,
      count(DISTINCT offer_links.sha) AS files
    FROM agreed_offers JOIN readouts ON readouts.key = agreed_offers.key AND readouts.offer = agreed_offers.offer
    JOIN offer_links ON offer_links.readout = readouts.id
    WHERE readouts.review IN ('quorum', 'approved') AND offer_links.sha NOT IN (SELECT sha FROM readout_firmware)
    GROUP BY agreed_offers.key, agreed_offers.offer
    ORDER BY agreed_offers.day, agreed_offers.key`).all();
  return results;
}

// Keys the admin view lists by name, most sent first.
const LINK_KEYS_SHOWN = 200;

// What the admin view shows of offers sent as links: readouts that gave
// links (`sent`) and the files their links fetched (`fetched`, `bytes`), in
// all and by key, each key's offers agreed beside them.
export async function linkFigures(db) {
  const totals = await db.prepare(`SELECT count(DISTINCT readout) AS sent, count(bytes) AS fetched,
    coalesce(sum(bytes), 0) AS bytes FROM offer_links`).first();
  const { results: keys } = await db.prepare(`SELECT readouts.key, count(DISTINCT readouts.id) AS sent,
      (SELECT count(*) FROM agreed_offers WHERE agreed_offers.key = readouts.key) AS agreed,
      count(offer_links.bytes) AS fetched, coalesce(sum(offer_links.bytes), 0) AS bytes
    FROM readouts JOIN offer_links ON offer_links.readout = readouts.id
    GROUP BY readouts.key ORDER BY sent DESC, readouts.key LIMIT ?`).bind(LINK_KEYS_SHOWN).all();
  return { ...totals, keys };
}
