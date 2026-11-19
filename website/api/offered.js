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
// - A link of a shape not seen before (`url`, linkParts) is kept for review
//   and never fetched until the admin view approves it (approveLink), only
//   peeked at once when first taken (peekNamed); rejected, never.
// - OFFER_FETCHES_DAILY fetches a day at most, of every kind. A file still
//   missing OFFER_FETCH_ALERT_DAYS after its offer was taken as right is told
//   to the maintainer (alerts.js): the sign to set OFFERED_SEND back to
//   "files" (wrangler.jsonc, .github/readouts.md).
import { release, reserve } from "./counters.js";
import { READOUTS_EVER } from "./pending.js";
import { hex, sha256 } from "./proof-of-work.js";

// eWeLink's file servers, one a region, by name (docs/espressif-research.md,
// "Learning the firmware name"): the first is where a link of its shape is
// fetched from; each is tried for a link to review that names its server by
// address.
const EWELINK_SERVERS = ["eu-otadl.coolkit.cc", "us-otadl.coolkit.cc", "as-otadl.coolkit.cc", "cn-otadl.coolkit.cn"]
  .map((name) => `http://${name}:8088`);

// Each cloud an offer may come from: the shape its links take, the part its
// readouts' key adds (index.js keyOf; none for eWeLink, so every key held
// from before, all eWeLink devices', stays as it is), where a link's path
// is fetched from: `base`, the path, then `query`; the servers a path is
// tried on for a link to review that names its server by address
// (`servers`); and the names of its own servers (`hosts`), the only ones a
// link to review may name.
// - eWeLink: a link names its server by address, which a Worker cannot fetch
//   (Cloudflare's error 1003), but every region's server gives every link's
//   path by name; the device's id, time and signature are asked for and not
//   checked (docs/espressif-research.md, "Learning the firmware name").
export const CLOUDS = {
  ewelink: {
    link: /^\/ota\/rom\/[A-Za-z0-9]{32,64}\/[A-Za-z0-9._-]{1,64}$/,
    key: "",
    base: EWELINK_SERVERS[0],
    servers: EWELINK_SERVERS,
    query: "deviceid=0&ts=1&sign=0",
    hosts: /^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+coolkit\.c[cn]$/,
  },
};

// A link of a shape not seen before (`url` in /v1/offers' files and a
// readout's offered entries, .github/readouts.md), kept for review: the
// whole link, scheme://host[:port]/path, as the tool checks it too
// (tools/ewelink/offered_firmware.py). At most URL_MOST characters, its path
// URL_PATH_MOST; a port of URL_PORTS or none.
export const URL_MOST = 300;
export const URL_PATH_MOST = 200;
export const URL_PORTS = [80, 443, 8088];
// Letters, digits and ". _ - /" in the path; no user@, no [IPv6], no %, no
// query or fragment, since none of their characters is taken.
const URL_SHAPE = /^(https?):\/\/([A-Za-z0-9.-]{1,253})(?::([0-9]{1,5}))?(\/[A-Za-z0-9._\/-]*)$/;
const OCTET = /^(?:0|[1-9][0-9]{0,2})$/;
// IPv4 ranges that are no public server's (RFC 6890's special-purpose
// registry), each [its leading octets, prefix length]: this network, private,
// CGNAT, loopback, link-local, IETF protocol assignments, the documentation
// ranges, the old 6to4 relay, benchmarking, multicast, and reserved with
// broadcast. Written as octets, not dotted, as none is an address.
const NOT_PUBLIC = [[[0], 8], [[10], 8], [[100, 64], 10], [[127], 8], [[169, 254], 16],
  [[172, 16], 12], [[192, 0, 0], 24], [[192, 0, 2], 24], [[192, 88, 99], 24], [[192, 168], 16],
  [[198, 18], 15], [[198, 51, 100], 24], [[203, 0, 113], 24], [[224], 4], [[240], 4]];

// An IPv4 address in dotted decimal as a number, or null: four parts, each
// 0 to 255 with no leading zero (which some read as octal).
function ipv4(host) {
  const parts = host.split(".");
  if (parts.length !== 4 || !parts.every((part) => OCTET.test(part) && Number(part) <= 255)) {
    return null;
  }
  return parts.reduce((value, part) => value * 256 + Number(part), 0);
}
const RANGES = NOT_PUBLIC.map(([octets, bits]) => [[0, 1, 2, 3].reduce((value, at) => value * 256 + (octets[at] ?? 0), 0),
  2 ** (32 - bits)]);
export const isPublicAddress = (host) => {
  const value = ipv4(host);
  return value !== null && !RANGES.some(([first, size]) => value >= first && value < first + size);
};

// A link to review taken apart: {scheme, host, port, path}, its host in
// lower case and `port` null for none; or null where it breaks a rule.
export function linkParts(url, cloud = CLOUDS.ewelink) {
  if (typeof url !== "string" || url.length > URL_MOST) {
    return null;
  }
  const match = URL_SHAPE.exec(url);
  if (!match) {
    return null;
  }
  const [, scheme, given, portText, path] = match;
  const host = given.toLowerCase();
  const port = portText === undefined ? null : Number(portText);
  if ((port !== null && (!URL_PORTS.includes(port) || String(port) !== portText))
      || !(cloud.hosts.test(host) || isPublicAddress(host))
      || path.length > URL_PATH_MOST || path.includes("..") || path.includes("//")) {
    return null;
  }
  return { scheme, host, port, path };
}

// A link as it was sent: a link to review whole, one of its cloud's shape
// its path alone. What it is known by in offer_fetch_failures and link_peeks.
export const sentAs = (link) => (link.host == null ? link.path
  : `${link.scheme}://${link.host}${link.port == null ? "" : `:${link.port}`}${link.path}`);
// The same, in SQL, of table `t`'s row.
const SENT_AS = (t) => `CASE WHEN ${t}.host IS NULL THEN ${t}.path ELSE ${t}.scheme || '://' || ${t}.host
  || coalesce(':' || ${t}.port, '') || ${t}.path END`;

// Where a link is fetched from, each tried in turn, `query` added to each
// (eWeLink's servers answer a request without it an empty 400): a link of
// its cloud's shape from `base`; a link to review that names one of its
// cloud's servers as it was sent; one that names its server by address never
// from it, but its path from each of `servers`, as eWeLink's give every
// link's path.
export function fetchedFrom(link, cloud = CLOUDS[link.cloud] ?? CLOUDS.ewelink) {
  if (link.host == null) {
    return [`${cloud.base}${link.path}?${cloud.query}`];
  }
  if (isPublicAddress(link.host)) {
    return cloud.servers.map((server) => `${server}${link.path}?${cloud.query}`);
  }
  return [`${sentAs(link)}?${cloud.query}`];
}

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
async function download(fetcher, url, init) {
  let response;
  try {
    response = await (init ? fetcher(url, init) : fetcher(url));
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

// Whether a link may be fetched: one of its cloud's shape, or one to review
// approved that still keeps every rule (linkParts). A link pending or
// rejected never is.
function fetchable(link, cloud) {
  if (link.host == null) {
    return (link.review ?? "auto") === "auto" && cloud.link.test(link.path);
  }
  return link.review === "approved" && linkParts(sentAs(link), cloud) !== null;
}

// One fetch counted against the day's (and on first sight the sender's,
// `sender`): whether there was room.
async function counted(env, day, sender) {
  if (sender && !(await reserve(env.DB, sender, day, { most: OFFER_FIRST_SIGHT_PER_SENDER }))) {
    return false;
  }
  if (!(await reserve(env.DB, fetchesOn(day), day, { most: OFFER_FETCHES_DAILY }))) {
    if (sender) {
      await release(env.DB, sender);
    }
    return false;
  }
  return true;
}

// One link of a file tried: {sha, cloud, path, readout, and for a link to
// review scheme, host, port, review}, the readout it is kept under if it
// gives the file; from each place it is fetched from (fetchedFrom) in turn,
// each fetch counted. "kept"; "held", kept meanwhile by another; "failed";
// or "capped", where the day's fetches, the sender's (`sender`, a counter's
// name, on first sight), or the store ran out. A link to review is fetched
// with no redirect followed: only the server approved answers.
async function tryLink(env, link, day, { totalBytes, fetcher, sender = null }) {
  const cloud = Object.hasOwn(CLOUDS, link.cloud) ? CLOUDS[link.cloud] : null;
  if (!cloud || !fetchable(link, cloud)) {
    return "failed";
  }
  let bytes = null;
  let failed = "none";
  for (const url of fetchedFrom(link, cloud)) {
    if (!(await counted(env, day, sender))) {
      return "capped";
    }
    const got = await download(fetcher, url, link.host == null ? undefined : { redirect: "manual" });
    if (got.bytes && hex(await sha256(got.bytes)) === link.sha) {
      bytes = got.bytes;
      break;
    }
    if (got.bytes || got.why === "other") {
      failed = "other";
    }
  }
  if (!bytes) {
    await env.DB.prepare(FAILED).bind(link.sha, link.cloud, sentAs(link), failed, day).run();
    return "failed";
  }
  if (!(await reserve(env.DB, READOUTS_EVER, null, { by: 0, bytes: bytes.byteLength, mostBytes: totalBytes }))) {
    return "capped";
  }
  return (await keepFile(env, link, bytes)) ? "kept" : "held";
}

// The reviews a link may be fetched with: of its cloud's shape, or approved.
const FETCHABLE = "('auto', 'approved')";

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
  const { results: links } = await env.DB.prepare(`SELECT sha, cloud, path, readout, review FROM offer_links
    WHERE readout = ?1 AND review = 'auto' AND NOT EXISTS (SELECT 1 FROM offer_fetch_failures AS failed
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
  // The files links that may be fetched were given for, by readouts of it
  // not rejected.
  const { results: files } = await env.DB.prepare(`SELECT DISTINCT offer_links.sha FROM offer_links
    JOIN readouts ON readouts.id = offer_links.readout
    WHERE readouts.key = ?1 AND readouts.offer = ?2 AND readouts.review != 'rejected'
      AND offer_links.review IN ${FETCHABLE}`).bind(key, offer).all();
  for (const { sha } of files) {
    if (done.capped || (await env.READOUTS.head(firmwareKey(sha))) !== null) {
      continue;
    }
    // The link most networks gave first: a file's link is the same for every
    // device, so made-up ones from one network come after the owners'. None
    // that gave another file, or gave nothing today; none pending or rejected.
    const { results: links } = await env.DB.prepare(`SELECT offer_links.sha, offer_links.cloud, offer_links.path,
      offer_links.scheme, offer_links.host, offer_links.port, offer_links.review,
      min(offer_links.readout) AS readout FROM offer_links JOIN readouts ON readouts.id = offer_links.readout
      LEFT JOIN readout_networks ON readout_networks.id = readouts.id
      WHERE readouts.key = ?1 AND readouts.offer = ?2 AND readouts.review != 'rejected' AND offer_links.sha = ?3
        AND offer_links.review IN ${FETCHABLE}
        AND NOT EXISTS (SELECT 1 FROM offer_fetch_failures AS failed WHERE failed.sha = offer_links.sha
          AND failed.cloud = offer_links.cloud AND failed.path = ${SENT_AS("offer_links")}
          AND (failed.why = 'other' OR failed.day = ?5))
      GROUP BY offer_links.cloud, ${SENT_AS("offer_links")}
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
  WHERE readouts.review IN ('quorum', 'approved') AND offer_links.review IN ${FETCHABLE}
    AND offer_links.sha NOT IN (SELECT sha FROM readout_firmware)`;

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
    WHERE readouts.review IN ('quorum', 'approved') AND offer_links.review IN ${FETCHABLE}
      AND offer_links.sha NOT IN (SELECT sha FROM readout_firmware)
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

// A link to review is peeked at once, when first taken (peekNamed), so the
// maintainer sees what it gives before approving it: its first PEEK_BYTES
// asked for with a Range, and no more read whatever the server sends; no
// redirect followed; PEEK_TIMEOUT_MS at most for each server tried.
export const PEEK_BYTES = 100;
export const PEEK_TIMEOUT_MS = 5000;
// What a file's first bytes start like, each kind's first bytes, in the
// order tried: Beken's FOTA container (FOTA_MAGIC, little-endian), Sonoff's
// wrapper (record format 3, then its record count, firmwareNames), an
// ESP8266 image's header, a zip. "unknown" for anything else.
export const PEEK_KINDS = [["beken-fota", [0x1e, 0xf1, 0xee, 0x1b]], ["sonoff-wrapper", [0x03, 0x01]],
  ["esp8266", [0xea, 0x04]], ["zip", [0x50, 0x4b]]];
export const kindOf = (bytes) => PEEK_KINDS.find(([, start]) => start.every((byte, at) => bytes[at] === byte))?.[0]
  ?? "unknown";
// The most of a redirect's Location kept: printable characters alone.
const LOCATION_MOST = 200;
const CONTENT_RANGE = /^bytes \d+-\d+\/(\d+)$/;

// The first PEEK_BYTES of `url`, or fewer: {outcome, status, size, location,
// bytes}, outcome "answered", "redirect", "timeout" or "failed".
async function peekAt(fetcher, url) {
  let response;
  try {
    response = await fetcher(url, { headers: { Range: `bytes=0-${PEEK_BYTES - 1}` }, redirect: "manual",
      signal: AbortSignal.timeout(PEEK_TIMEOUT_MS) });
  } catch (error) {
    return { outcome: error?.name === "TimeoutError" || error?.name === "AbortError" ? "timeout" : "failed" };
  }
  const status = response.status;
  if (status >= 300 && status < 400) {
    await response.body?.cancel();
    const location = (response.headers.get("Location") ?? "").replace(/[^\x20-\x7e]/g, "").slice(0, LOCATION_MOST);
    return { outcome: "redirect", status, location };
  }
  const size = CONTENT_RANGE.exec(response.headers.get("Content-Range") ?? "")?.[1];
  const bytes = new Uint8Array(PEEK_BYTES);
  let got = 0;
  try {
    const reader = response.body?.getReader();
    while (reader && got < PEEK_BYTES) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      const part = value.subarray(0, PEEK_BYTES - got);
      bytes.set(part, got);
      got += part.byteLength;
    }
    await reader?.cancel();
  } catch (error) {
    if (!got) {
      return { outcome: error?.name === "TimeoutError" ? "timeout" : "failed", status };
    }
  }
  return { outcome: "answered", status, size: size === undefined ? null : Number(size), bytes: bytes.subarray(0, got) };
}

// A link claimed for its one peek, before it is made: whether this call
// claimed it, so two sent at once peek once.
const PEEK_CLAIMED = `INSERT INTO link_peeks (cloud, link, day) VALUES (?1, ?2, ?3) ON CONFLICT DO NOTHING
  RETURNING link`;
const PEEK_KEPT = `UPDATE link_peeks SET outcome = ?3, status = ?4, size = ?5, server = ?6, location = ?7, kind = ?8,
  names = ?9, hex = ?10 WHERE cloud = ?1 AND link = ?2`;

// Each link to review readout `readout` gave, never peeked at, peeked at
// once: from where approving it would fetch it (fetchedFrom), so a server
// named by address is never connected to, the first of them that answers
// 2xx kept, or the last's answer. Each request counted as a fetch, against
// the day's and the sender's (`sender`, as fetchNamed takes it); a link the
// day has no room for is peeked at when next sent. What it did: {peeked, capped}.
export async function peekNamed(env, readout, now, { sender, fetcher = fetch }) {
  const day = now.toISOString().slice(0, 10);
  const done = { peeked: 0, capped: false };
  const { results: links } = await env.DB.prepare(`SELECT sha, cloud, path, scheme, host, port FROM offer_links
    WHERE readout = ?1 AND review = 'pending' AND NOT EXISTS (SELECT 1 FROM link_peeks
      WHERE link_peeks.cloud = offer_links.cloud AND link_peeks.link = ${SENT_AS("offer_links")})
    ORDER BY sha`).bind(readout).all();
  for (const link of links) {
    const cloud = Object.hasOwn(CLOUDS, link.cloud) ? CLOUDS[link.cloud] : null;
    if (!cloud || !linkParts(sentAs(link), cloud)) {
      continue;
    }
    if (!(await env.DB.prepare(PEEK_CLAIMED).bind(link.cloud, sentAs(link), day).first())) {
      continue;   // peeked at meanwhile by another sent at once
    }
    let seen = null;
    for (const url of fetchedFrom(link, cloud)) {
      if (!(await counted(env, day, sender))) {
        done.capped = true;
        break;
      }
      seen = { ...(await peekAt(fetcher, url)), server: new URL(url).host };
      if (seen.outcome === "answered" && seen.status >= 200 && seen.status < 300) {
        break;
      }
    }
    if (!seen) {
      await env.DB.prepare("DELETE FROM link_peeks WHERE cloud = ?1 AND link = ?2").bind(link.cloud, sentAs(link)).run();
      break;
    }
    const bytes = seen.bytes ?? null;
    const names = bytes ? firmwareNames(bytes) : [];
    await env.DB.prepare(PEEK_KEPT).bind(link.cloud, sentAs(link), seen.outcome, seen.status ?? null, seen.size ?? null,
      seen.server, seen.location ?? null, bytes ? kindOf(bytes) : null, names.length ? names.join("+") : null,
      bytes ? hex(bytes) : null).run();
    done.peeked += 1;
    if (done.capped) {
      break;
    }
  }
  return done;
}

// The rows of one link to review, wherever it was sent: those of the same
// file, cloud and parts as readout ?1's link of file ?2.
const SAME_LINK = `sha = ?2 AND (cloud, path, scheme, host, port IS NULL, coalesce(port, 0)) = (SELECT cloud, path,
  scheme, host, port IS NULL, coalesce(port, 0) FROM offer_links WHERE readout = ?1 AND sha = ?2 AND host IS NOT NULL)`;

// A link to review rejected, wherever it was sent: never fetched. Whether one was.
export async function rejectLink(db, readout, sha) {
  const { results } = await db.prepare(`UPDATE offer_links SET review = 'rejected'
    WHERE review = 'pending' AND ${SAME_LINK} RETURNING readout`).bind(readout, sha).all();
  return results.length > 0;
}

// A link to review approved, wherever it was sent (or one approved tried
// again), and its file fetched by it if not held, as tryLink fetches one,
// within the day's fetches: "kept", "held", "failed", "capped", or "none",
// no such link pending or approved.
export async function approveLink(env, readout, sha, now, { totalBytes, fetcher = fetch }) {
  const { results } = await env.DB.prepare(`UPDATE offer_links SET review = 'approved'
    WHERE review IN ('pending', 'approved') AND ${SAME_LINK}
    RETURNING readout, sha, cloud, path, scheme, host, port, review`).bind(readout, sha).all();
  if (!results.length) {
    return "none";
  }
  if ((await env.READOUTS.head(firmwareKey(sha))) !== null) {
    return "held";
  }
  const link = results.find((row) => row.readout === readout) ?? results[0];
  return tryLink(env, link, now.toISOString().slice(0, 10), { totalBytes, fetcher });
}

// Links to review the admin view lists at most, newest first.
const REVIEW_SHOWN = 100;

// The links to review the admin view lists: each pending one, or approved
// and its file not held, once however many sent it, with what its readouts
// say of the device (`key`), how many networks sent it, when it was first
// seen, its peek (link_peeks) and whether a fetch of it failed.
export async function linksToReview(db) {
  const { results } = await db.prepare(`SELECT offer_links.sha, offer_links.cloud, offer_links.path,
      offer_links.scheme, offer_links.host, offer_links.port, offer_links.review, min(offer_links.readout) AS readout,
      min(readouts.key) AS key, count(DISTINCT readout_networks.network) AS networks,
      min(readouts.received) AS first, link_peeks.outcome, link_peeks.status, link_peeks.size, link_peeks.server,
      link_peeks.location, link_peeks.kind, link_peeks.names, link_peeks.hex,
      EXISTS (SELECT 1 FROM offer_fetch_failures AS failed WHERE failed.sha = offer_links.sha
        AND failed.cloud = offer_links.cloud AND failed.path = ${SENT_AS("offer_links")}) AS failed
    FROM offer_links JOIN readouts ON readouts.id = offer_links.readout
    LEFT JOIN readout_networks ON readout_networks.id = readouts.id
    LEFT JOIN link_peeks ON link_peeks.cloud = offer_links.cloud AND link_peeks.link = ${SENT_AS("offer_links")}
    WHERE offer_links.host IS NOT NULL AND (offer_links.review = 'pending'
      OR (offer_links.review = 'approved' AND offer_links.sha NOT IN (SELECT sha FROM readout_firmware)))
    GROUP BY offer_links.sha, offer_links.cloud, ${SENT_AS("offer_links")}
    ORDER BY offer_links.review DESC, first DESC LIMIT ?`).bind(REVIEW_SHOWN).all();
  return results;
}

// Links the admin view lists at most, newest first.
const LINKS_SHOWN = 200;

// Every link the API was given, once each, for the admin view to copy out:
// its file, its device's key, its review, whether its file is held, and how
// many readouts gave it.
export async function everyLink(db) {
  const { results } = await db.prepare(`SELECT offer_links.sha, offer_links.cloud, offer_links.path,
      offer_links.scheme, offer_links.host, offer_links.port, offer_links.review, min(readouts.key) AS key,
      count(*) AS sent, offer_links.sha IN (SELECT sha FROM readout_firmware) AS held, max(readouts.received) AS last
    FROM offer_links JOIN readouts ON readouts.id = offer_links.readout
    GROUP BY offer_links.sha, offer_links.cloud, ${SENT_AS("offer_links")}, offer_links.review
    ORDER BY last DESC LIMIT ?`).bind(LINKS_SHOWN).all();
  return results;
}
