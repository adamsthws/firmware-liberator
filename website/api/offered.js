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
//   peeked at once when first taken (peekNamed); rejected, never. At most
//   PENDING_LINKS_MOST wait at once; one past it is not kept (index.js LINKS).
// - Nothing of the links grows without bound (linkPruning, nightly.js): a link
//   pending review goes PENDING_LINK_DAYS after the readout that sent it,
//   with its peek; a peek or a failure goes once no readout gives its link,
//   a failure FAILURE_DAYS after its last try; the newest REJECTED_LINKS_MOST
//   rejections are kept, apart from the readouts, so a link rejected stays so.
// - Two budgets a day: FIRST_SIGHT_DAILY for first sight and peeks, whose
//   links no one has agreed on yet, and OFFER_FETCHES_DAILY for offers taken
//   as right and links approved, so made-up links can never use up the
//   fetches owners' agreed offers need. A file still missing
//   OFFER_FETCH_ALERT_DAYS after its offer was taken as right is told to the
//   maintainer (alerts.js): the sign to set OFFERED_SEND back to "files"
//   (wrangler.jsonc, .github/readouts.md).
import { release, reserve } from "./counters.js";
import { PENDING, PENDING_DAYS, READOUTS_EVER } from "./pending.js";
import { hex, sha256 } from "./proof-of-work.js";

const DAY = 24 * 60 * 60 * 1000;

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

// Links to review waiting at most, each counted once however many sent it:
// the admin view lists each with its first bytes for a person to look at, and
// a hundred is an evening's review; a genuine new shape of link is eWeLink
// changing its servers, which all its devices share, so owners' links come
// to a few, and only made-up ones would fill it. Past it, a link to review
// not already waiting is not kept (index.js LINKS), nor peeked at, and its
// readout or offer is taken as it would be; each counted a day
// (linksNotKeptOn), for the admin view. A readout's own links, MAX_FIRMWARE
// at most (index.js), are weighed together, so may pass it by as many less one.
export const PENDING_LINKS_MOST = 100;
// Of those, how many may wait that were sent only from networks that count
// as none (networks.js: a host's, a VPN's): index.js RENTED_SHARE of
// PENDING_LINKS_MOST (a test holds the two alike), so a few rented servers'
// made-up links cannot fill the queue owners' new links need.
export const PENDING_LINKS_RENTED = 10;
export const linksNotKeptOn = (day) => `links-not-kept:${day}`;
// Links to review one sender may put in the queue a day, counted as
// first sight's fetches are (index.js firstSightSender): an account's devices
// offered a link of a new shape come to a file or two each, so this is a few
// kinds of device; more from one sender wait for the next day, or for an
// owner elsewhere to send them. One /24 cannot fill the queue alone.
export const PENDING_LINKS_PER_SENDER = 5;
export const reviewLinksOf = (hash, day) => `review-links:${hash}:${day}`;
// A link pending review is deleted this long after the readout that sent it,
// as a readout waiting for review is (pending.js): it is sent again by the
// next owner whose device is offered it.
export const PENDING_LINK_DAYS = PENDING_DAYS;
// A link's failure (offer_fetch_failures) is forgotten this long after it was
// last tried, as long as its readout may wait: after it, one more try of a
// made-up link costs one fetch, within the day's.
export const FAILURE_DAYS = PENDING_DAYS;
// Rejections kept (rejected_links), the newest: each is the maintainer's
// click on the admin view, with PENDING_LINKS_MOST waiting at most, so this
// many is years of reviewing, and some 4 MB. Kept by count, not age, so a
// made-up link sent again and again never comes back for review while
// reviewing goes on.
export const REJECTED_LINKS_MOST = 10_000;

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
export const SENT_AS = (t) => `CASE WHEN ${t}.host IS NULL THEN ${t}.path ELSE ${t}.scheme || '://' || ${t}.host
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

// Fetches a day at most for offers taken as right (on agreement and each
// night after) and for links approved, across everyone: each file is fetched
// once, so this is room for a day's new releases many times over. Apart from
// first sight's, so no number of made-up links can use it up.
export const OFFER_FETCHES_DAILY = 50;
// Fetches a day at most on first sight, peeks included, across everyone:
// what a file fetched before its offer agrees, or a peek, may cost. One past
// it is no failure: its offer's agreement, or the nightly retry, fetches it.
export const FIRST_SIGHT_DAILY = 50;
// Of those, what senders on a network that counts as none (networks.js: a
// host's, a VPN's) may take: index.js RENTED_SHARE of FIRST_SIGHT_DAILY (a
// test holds the two alike), so rented servers cannot use up owners' day.
export const FIRST_SIGHT_RENTED_DAILY = 5;
// First-sight fetches a day from one sender's offers and readouts, counted by
// a keyed hash of its address, an IPv4 one by its /24 (index.js
// firstSightSender): so one sender's made-up links, even from a rented block
// of addresses, cannot use up FIRST_SIGHT_DAILY before the real owners' files
// are tried. Fetches for offers taken as right are not held to it.
export const OFFER_FIRST_SIGHT_PER_SENDER = 5;
// The longest one fetch may take, its body read included: a server that
// answers slowly, or never ends its body, holds up the Worker no longer.
// Ample for OFFER_FETCH_MAX_BYTES from eWeLink's servers.
export const OFFER_FETCH_TIMEOUT_MS = 20_000;
// How long after its answer a request's fetches and peeks may go on (index.js
// fetchFiles: their `until`): Cloudflare gives what a Worker waits on after
// its answer 30 seconds, and the rest is for keeping what the last fetched.
// Each fetch's own timeout is cut to end by then, and none is begun with
// less than FETCH_LEAST_MS left: one past it is fetched when its file is
// next sent, or its offer agrees.
export const AFTER_ANSWER_MS = 25_000;
const FETCH_LEAST_MS = 2000;
// How long a fetch may take, `most` at most, to end by `until` (epoch
// milliseconds): null where too little time is left to begin one.
const timeFor = (most, until) => {
  const left = Math.min(most, until - Date.now());
  return left >= FETCH_LEAST_MS ? left : null;
};
// The most a file fetched may be: as tools/ewelink/offered_firmware.py's
// MAX_SIZE, more than any Sonoff update file.
export const OFFER_FETCH_MAX_BYTES = 8 * 1024 * 1024;
// Links tried for one file of an offer taken as right, each a different one
// given for it.
export const OFFER_FETCH_PATHS = 3;
// Offers tried again a night at most (retryOffers): with OFFER_FETCHES_DAILY,
// what holds the night's queries to a few dozen; any more the next night,
// those tried longest ago first, so offers whose files never come cannot keep
// the rest from being tried.
export const OFFER_RETRY_EACH_NIGHT = 5;
// Days a link that gives nothing is tried, from its first failure: a month of
// a file missing is eWeLink no longer giving it, not an outage, and the
// nightly email has named it every OFFER_FETCH_ALERT_DAYS since. Its failure
// forgotten (FAILURE_DAYS after its last try), it is tried again.
export const OFFER_RETRY_DAYS = 30;
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

// The most of a file's firmware names kept, joined by "+": R2 holds 2 KB of
// an object's custom metadata in all, and a wrapper may list up to 255 names
// of 32 bytes. Whole names only, in order, up to this many characters.
export const NAMES_KEPT_MOST = 1024;
// The names as kept, or null for none.
export function namesKept(names) {
  let kept = "";
  for (const name of names) {
    const longer = kept ? `${kept}+${name}` : name;
    if (longer.length > NAMES_KEPT_MOST) {
      break;
    }
    kept = longer;
  }
  return kept || null;
}

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

// The day's counts of files fetched (counters.js): for offers taken as right
// and links approved; on first sight; on first sight from rented networks.
export const fetchesOn = (day) => `offer-fetches:${day}`;
export const firstSightsOn = (day) => `first-sights:${day}`;
export const rentedFirstSightsOn = (day) => `rented-first-sights:${day}`;
// A sender's day count of first-sight fetches (counters.js), by the keyed
// hash index.js names it with.
export const firstSightOf = (hash, day) => `first-sight:${hash}:${day}`;
const firmwareKey = (sha) => `readouts/firmware/${sha}`;

// What `url` gave: {bytes}, or {why}: "none", nothing given, or "other",
// more than OFFER_FETCH_MAX_BYTES, which no file of the cloud's is.
async function download(fetcher, url, init, timeout) {
  let response;
  try {
    response = await fetcher(url, { ...init, signal: AbortSignal.timeout(timeout) });
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

// A link that did not give its file, noted (offer_fetch_failures), the day
// it first failed kept.
const FAILED = `INSERT INTO offer_fetch_failures (sha, cloud, path, why, day, since) VALUES (?1, ?2, ?3, ?4, ?5, ?5)
  ON CONFLICT DO UPDATE SET why = excluded.why, day = excluded.day`;

// Whether a link may be fetched: one of its cloud's shape, or one to review
// approved that still keeps every rule (linkParts). A link pending or
// rejected never is; one that no longer keeps the rules is noted as failed
// (tryLink), as though it gave another file, so the nightly retry
// (UNFETCHED) does not keep a place for it.
function fetchable(link, cloud) {
  if (link.host == null) {
    return (link.review ?? "auto") === "auto" && cloud.link.test(link.path);
  }
  return link.review === "approved" && linkParts(sentAs(link), cloud) !== null;
}

// One fetch counted: on first sight (`sight`, {sender, rented}: the name of
// the sender's day count, firstSightOf, and whether it is on a network that
// counts as none) against the sender's, the day's first sights and, rented,
// their share; else against OFFER_FETCHES_DAILY. Whether there was room: if
// not, nothing is counted.
async function counted(env, day, sight) {
  const limits = sight ? [[sight.sender, OFFER_FIRST_SIGHT_PER_SENDER], [firstSightsOn(day), FIRST_SIGHT_DAILY],
    ...(sight.rented ? [[rentedFirstSightsOn(day), FIRST_SIGHT_RENTED_DAILY]] : [])]
    : [[fetchesOn(day), OFFER_FETCHES_DAILY]];
  const taken = [];
  for (const [name, most] of limits) {
    if (!(await reserve(env.DB, name, day, { most }))) {
      for (const each of taken) {
        await release(env.DB, each);
      }
      return false;
    }
    taken.push(name);
  }
  return true;
}

// One link of a file tried: {sha, cloud, path, readout, and for a link to
// review scheme, host, port, review}, the readout it is kept under if it
// gives the file; from each place it is fetched from (fetchedFrom) in turn,
// each fetch counted (counted; `sight` on first sight). "kept"; "held", kept
// meanwhile by another; "failed"; or "capped", where the day's fetches, or the
// store, or the time after the answer (`until`, AFTER_ANSWER_MS), ran out. A
// link to review is fetched with no redirect followed: only the server
// approved answers.
async function tryLink(env, link, day, { totalBytes, fetcher, sight = null, until = Infinity }) {
  const cloud = Object.hasOwn(CLOUDS, link.cloud) ? CLOUDS[link.cloud] : null;
  if (!cloud || !fetchable(link, cloud)) {
    if (cloud && link.review !== "pending" && link.review !== "rejected") {
      await env.DB.prepare(FAILED).bind(link.sha, link.cloud, sentAs(link), "other", day).run();
    }
    return "failed";
  }
  let bytes = null;
  let failed = "none";
  for (const url of fetchedFrom(link, cloud)) {
    const timeout = timeFor(OFFER_FETCH_TIMEOUT_MS, until);
    if (timeout === null || !(await counted(env, day, sight))) {
      return "capped";
    }
    const got = await download(fetcher, url, link.host == null ? {} : { redirect: "manual" }, timeout);
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
// `totalBytes`: the store's limit (index.js TOTAL_BYTES); `sight`, its sender
// as counted takes it. What it did: {fetched, missing, capped}.
export async function fetchNamed(env, readout, now, { totalBytes, sight, fetcher = fetch, until }) {
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
    const outcome = await tryLink(env, link, day, { totalBytes, fetcher, sight, until });
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
export async function fetchOffer(env, key, offer, now, { totalBytes, fetcher = fetch, until }) {
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
    // that gave another file, or gave nothing today or for OFFER_RETRY_DAYS;
    // none pending or rejected.
    const { results: links } = await env.DB.prepare(`SELECT offer_links.sha, offer_links.cloud, offer_links.path,
      offer_links.scheme, offer_links.host, offer_links.port, offer_links.review,
      min(offer_links.readout) AS readout FROM offer_links JOIN readouts ON readouts.id = offer_links.readout
      LEFT JOIN readout_networks ON readout_networks.id = readouts.id
      WHERE readouts.key = ?1 AND readouts.offer = ?2 AND readouts.review != 'rejected' AND offer_links.sha = ?3
        AND offer_links.review IN ${FETCHABLE}
        AND NOT EXISTS (SELECT 1 FROM offer_fetch_failures AS failed WHERE failed.sha = offer_links.sha
          AND failed.cloud = offer_links.cloud AND failed.path = ${SENT_AS("offer_links")}
          AND (failed.why = 'other' OR failed.day = ?5 OR failed.since < ?6))
      GROUP BY offer_links.cloud, ${SENT_AS("offer_links")}
      ORDER BY count(DISTINCT readout_networks.network) DESC, min(readouts.received) LIMIT ?4`)
      .bind(key, offer, sha, OFFER_FETCH_PATHS, day, retrySince(day)).all();
    let outcome = "failed";
    for (const link of links) {
      outcome = await tryLink(env, link, day, { totalBytes, fetcher, until });
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
// its link's, so deleting the readout gives them back, and to those of
// readouts waiting for review (PENDING) while its readout is one, as taking
// it as right or deleting it gives back its bytes from there. Whether it was
// kept: if not, the store's bytes reserved for it are given back. Listed but
// not in the store, its keeping cut off (a Worker stopped after its answer),
// it is put there now, under the readout that lists it.
async function keepFile(env, { readout, sha }, bytes) {
  const size = bytes.byteLength;
  const claimed = await env.DB.prepare(`INSERT INTO readout_firmware (readout, sha) SELECT ?1, ?2
    WHERE NOT EXISTS (SELECT 1 FROM readout_firmware WHERE sha = ?2) RETURNING sha`).bind(readout, sha).first();
  if (!claimed) {
    await release(env.DB, READOUTS_EVER, size, 0);
    if ((await env.READOUTS.head(firmwareKey(sha))) === null) {
      const lister = await env.DB.prepare(`SELECT readouts.object FROM readout_firmware
        JOIN readouts ON readouts.id = readout_firmware.readout WHERE readout_firmware.sha = ?1
        ORDER BY readout_firmware.readout LIMIT 1`).bind(sha).first("object");
      const names = namesKept(firmwareNames(bytes));
      await env.READOUTS.put(firmwareKey(sha), bytes, { customMetadata: { readout: lister ?? "", fetched: "true",
        ...(names ? { names } : {}) } });
    }
    return false;
  }
  const object = await env.DB.prepare("SELECT object FROM readouts WHERE id = ?").bind(readout).first("object");
  const waiting = (sign) => env.DB.prepare(`UPDATE counters SET bytes = max(bytes ${sign} ?2, 0) WHERE name = ?3
    AND EXISTS (SELECT 1 FROM readouts WHERE id = ?1 AND review = 'pending')`).bind(readout, size, PENDING);
  await env.DB.batch([
    waiting("+"),
    env.DB.prepare("UPDATE readouts SET bytes = bytes + ?2 WHERE id = ?1").bind(readout, size),
    env.DB.prepare("UPDATE offer_links SET bytes = ?3 WHERE readout = ?1 AND sha = ?2").bind(readout, sha, size),
    env.DB.prepare("DELETE FROM offer_fetch_failures WHERE sha = ?").bind(sha),
  ]);
  const names = namesKept(firmwareNames(bytes));
  try {
    await env.READOUTS.put(firmwareKey(sha), bytes, { customMetadata: { readout: object ?? "", fetched: "true",
      ...(names ? { names } : {}) } });
  } catch (error) {
    await env.DB.batch([
      waiting("-"),
      env.DB.prepare("DELETE FROM readout_firmware WHERE readout = ?1 AND sha = ?2").bind(readout, sha),
      env.DB.prepare("UPDATE readouts SET bytes = bytes - ?2 WHERE id = ?1").bind(readout, size),
      env.DB.prepare("UPDATE offer_links SET bytes = NULL WHERE readout = ?1 AND sha = ?2").bind(readout, sha),
    ]);
    await release(env.DB, READOUTS_EVER, size, 0);
    throw error;
  }
  return true;
}

// The day before which a link's first failure means it is tried no more
// (OFFER_RETRY_DAYS).
const retrySince = (day) => new Date(Date.parse(`${day}T00:00:00Z`) - OFFER_RETRY_DAYS * DAY).toISOString()
  .slice(0, 10);

// The offers taken as right with a file linked and not held, of first stages
// and whole readouts taken as right, agreed (quorum) or approved, that a link
// is still tried for: none that gave another file, nor one first failing
// before ?2 (retrySince). ?1 at most, those tried longest ago first: by the
// last day a link of theirs failed, never first.
const UNFETCHED = `SELECT readouts.key, readouts.offer, max(failed.day) AS tried FROM offer_links
  JOIN readouts ON readouts.id = offer_links.readout
  LEFT JOIN offer_fetch_failures AS failed ON failed.sha = offer_links.sha AND failed.cloud = offer_links.cloud
    AND failed.path = ${SENT_AS("offer_links")}
  WHERE readouts.review IN ('quorum', 'approved') AND offer_links.review IN ${FETCHABLE}
    AND offer_links.sha NOT IN (SELECT sha FROM readout_firmware)
    AND (failed.why IS NULL OR (failed.why = 'none' AND failed.since >= ?2))
  GROUP BY readouts.key, readouts.offer
  ORDER BY tried IS NOT NULL, tried, readouts.key, readouts.offer LIMIT ?1`;

// The nightly job's part (nightly.js): each offer taken as right whose files
// are not all held, tried again within the day's fetches, OFFER_RETRY_EACH_NIGHT
// at most (UNFETCHED). What it did: {offers, fetched, missing}.
export async function retryOffers(env, now, { totalBytes, fetcher = fetch }) {
  const { results } = await env.DB.prepare(UNFETCHED)
    .bind(OFFER_RETRY_EACH_NIGHT, retrySince(now.toISOString().slice(0, 10))).all();
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
async function peekAt(fetcher, url, timeout) {
  let response;
  try {
    response = await fetcher(url, { headers: { Range: `bytes=0-${PEEK_BYTES - 1}` }, redirect: "manual",
      signal: AbortSignal.timeout(timeout) });
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
// claimed it, so two sent at once peek once. A claim whose peek was never
// kept (no server: the Worker stopped first) may be claimed again a later day.
const PEEK_CLAIMED = `INSERT INTO link_peeks (cloud, link, day) VALUES (?1, ?2, ?3)
  ON CONFLICT DO UPDATE SET day = excluded.day WHERE link_peeks.server IS NULL AND link_peeks.day < excluded.day
  RETURNING link`;
const PEEK_KEPT = `UPDATE link_peeks SET outcome = ?3, status = ?4, size = ?5, server = ?6, location = ?7, kind = ?8,
  names = ?9, hex = ?10 WHERE cloud = ?1 AND link = ?2`;

// Each link to review readout `readout` gave, never peeked at, peeked at
// once: from where approving it would fetch it (fetchedFrom), so a server
// named by address is never connected to, the first of them that answers
// 2xx kept, or the last's answer. Each request counted as a first-sight
// fetch (`sight`, as fetchNamed takes it); a link the day has no room for is
// peeked at when next sent, as is one past the time after the answer
// (`until`, as tryLink takes it). What it did: {peeked, capped}.
export async function peekNamed(env, readout, now, { sight, fetcher = fetch, until = Infinity }) {
  const day = now.toISOString().slice(0, 10);
  const done = { peeked: 0, capped: false };
  const { results: links } = await env.DB.prepare(`SELECT sha, cloud, path, scheme, host, port FROM offer_links
    WHERE readout = ?1 AND review = 'pending' AND NOT EXISTS (SELECT 1 FROM link_peeks
      WHERE link_peeks.cloud = offer_links.cloud AND link_peeks.link = ${SENT_AS("offer_links")}
        AND (link_peeks.server IS NOT NULL OR link_peeks.day >= ?2))
    ORDER BY sha`).bind(readout, day).all();
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
      const timeout = timeFor(PEEK_TIMEOUT_MS, until);
      if (timeout === null || !(await counted(env, day, sight))) {
        done.capped = true;
        break;
      }
      seen = { ...(await peekAt(fetcher, url, timeout)), server: new URL(url).host };
      if (seen.outcome === "answered" && seen.status >= 200 && seen.status < 300) {
        break;
      }
    }
    if (!seen) {
      await env.DB.prepare("DELETE FROM link_peeks WHERE cloud = ?1 AND link = ?2").bind(link.cloud, sentAs(link)).run();
      break;
    }
    const bytes = seen.bytes ?? null;
    const names = bytes ? namesKept(firmwareNames(bytes)) : null;
    await env.DB.prepare(PEEK_KEPT).bind(link.cloud, sentAs(link), seen.outcome, seen.status ?? null, seen.size ?? null,
      seen.server, seen.location ?? null, bytes ? kindOf(bytes) : null, names,
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

// A link to review rejected, wherever it was sent: never fetched, and noted
// apart from the readouts that sent it (rejected_links), so sent again after
// they are deleted it is still taken as rejected. Whether one was.
export async function rejectLink(db, readout, sha, now = new Date()) {
  // Both or neither: noted apart first, while it is still pending.
  const [, rejected] = await db.batch([
    db.prepare(`INSERT OR REPLACE INTO rejected_links (sha, cloud, link, day)
      SELECT sha, cloud, ${SENT_AS("offer_links")}, ?3 FROM offer_links WHERE readout = ?1 AND sha = ?2 AND host IS NOT NULL
        AND EXISTS (SELECT 1 FROM offer_links WHERE review = 'pending' AND ${SAME_LINK})`)
      .bind(readout, sha, now.toISOString().slice(0, 10)),
    db.prepare(`UPDATE offer_links SET review = 'rejected'
      WHERE review = 'pending' AND ${SAME_LINK} RETURNING readout`).bind(readout, sha),
  ]);
  return rejected.results.length > 0;
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

// Links to review the admin view lists at most: every one pending first
// (PENDING_LINKS_MOST, passed only by one readout's own links), then those
// approved and not held; each those sent from the most networks first, then
// the longest waiting, so one network's flood comes after owners' links.
const REVIEW_SHOWN = 2 * PENDING_LINKS_MOST;

// The links to review the admin view lists (REVIEW_SHOWN): each pending one,
// or approved and its file not held, once however many sent it, with what its
// readouts say of the device (`key`), how many networks sent it, when it was
// first seen, its peek (link_peeks) and whether a fetch of it failed.
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
    ORDER BY offer_links.review DESC, networks DESC, first LIMIT ?`).bind(REVIEW_SHOWN).all();
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

// The nightly job's part (nightly.js), in its batch after pending.js has
// deleted the readouts kept too long, taking their links: the statements
// deleting each link pending review sent PENDING_LINK_DAYS ago by a readout
// still kept (one taken as right, or disputing); each peek and failure no
// link left gives, and each failure last tried FAILURE_DAYS ago; and the
// rejections past the newest REJECTED_LINKS_MOST. By what each deletes.
export function linkPruning(db, today) {
  const before = (days) => new Date(Date.parse(`${today}T00:00:00Z`) - days * DAY).toISOString();
  return {
    links: db.prepare(`DELETE FROM offer_links WHERE review = 'pending'
      AND readout IN (SELECT id FROM readouts WHERE received < ?)`).bind(before(PENDING_LINK_DAYS)),
    peeks: db.prepare(`DELETE FROM link_peeks WHERE (cloud, link) NOT IN
      (SELECT cloud, ${SENT_AS("offer_links")} FROM offer_links WHERE host IS NOT NULL)`),
    failures: db.prepare(`DELETE FROM offer_fetch_failures WHERE day < ?
      OR (sha, cloud, path) NOT IN (SELECT sha, cloud, ${SENT_AS("offer_links")} FROM offer_links)`)
      .bind(before(FAILURE_DAYS).slice(0, 10)),
    rejected: db.prepare(`DELETE FROM rejected_links WHERE (sha, cloud, link) IN (SELECT sha, cloud, link
      FROM rejected_links ORDER BY day DESC, sha, cloud, link LIMIT -1 OFFSET ?)`).bind(REJECTED_LINKS_MOST),
  };
}
