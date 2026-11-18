// The files of an offer taken as right that a first stage sent only links for
// (/v1/offers, index.js), fetched by the API itself from the cloud that
// offers them, as the device would fetch them:
// - Only once the offer is taken as right (index.js OFFER_AGREES), and only
//   the files of it not held already: so no flood of made-up offers becomes a
//   flood of requests to the cloud, and each file is fetched once.
// - A file is kept only if its SHA-256 is the one the offer agreed on: a
//   made-up link fetches a file that does not match, and is dropped. Each
//   other link given for that file is tried next, those from the most
//   networks first, OFFER_FETCH_PATHS at most.
// - Kept as a readout's offered file is marked (readouts/firmware/<sha>, its
//   readout_firmware row listing it under the readout whose link fetched it),
//   the mark holding the file itself: a readout deleted takes it with it.
//   Its bytes count towards the store's limit (TOTAL_BYTES) and its readout's.
// - A file the cloud would not give is tried again each night (retryOffers,
//   nightly.js); one still missing OFFER_FETCH_ALERT_DAYS after its offer was
//   taken as right is told to the maintainer (alerts.js): the sign to set
//   OFFERED_SEND back to "files" (wrangler.jsonc, .github/readouts.md).
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

// Files fetched a day at most, across everyone: only offers newly taken as
// right are fetched, so this is room for a day's new releases many times over.
export const OFFER_FETCHES_DAILY = 50;
// The most a file fetched may be: as tools/ewelink/offered_firmware.py's
// MAX_SIZE, more than any Sonoff update file.
export const OFFER_FETCH_MAX_BYTES = 8 * 1024 * 1024;
// Links tried for one file, each a different one given for it.
export const OFFER_FETCH_PATHS = 3;
// Offers tried again a night at most (retryOffers): with OFFER_FETCHES_DAILY,
// what holds the night's queries to a few dozen; any more the next night.
export const OFFER_RETRY_EACH_NIGHT = 5;
// Days after its offer was taken as right that a file still not fetched is
// told to the maintainer, and each as many days after.
export const OFFER_FETCH_ALERT_DAYS = 3;

// The day's count of files fetched (counters.js).
const fetchesOn = (day) => `offer-fetches:${day}`;
const firmwareKey = (sha) => `readouts/firmware/${sha}`;

// The bytes at `url`, or null: not given, or more than OFFER_FETCH_MAX_BYTES.
async function download(fetcher, url) {
  let response;
  try {
    response = await fetcher(url);
  } catch {
    return null;
  }
  if (!response.ok || !response.body || Number(response.headers.get("Content-Length")) > OFFER_FETCH_MAX_BYTES) {
    await response.body?.cancel();
    return null;
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
        return null;
      }
      chunks.push(value);
    }
  } catch {
    return null;
  }
  const bytes = new Uint8Array(size);
  let at = 0;
  for (const chunk of chunks) {
    bytes.set(chunk, at);
    at += chunk.byteLength;
  }
  return bytes;
}


// Each file of offer `offer` of `key` that links were given for and is not
// held, fetched and kept if it is what the offer agreed on. `totalBytes`: the
// store's limit (index.js TOTAL_BYTES). What it did: {fetched, missing,
// capped}, capped where the day's fetches, or the store, ran out.
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
    // device, so made-up ones from one network come after the owners'.
    const { results: links } = await env.DB.prepare(`SELECT offer_links.cloud, offer_links.path,
      min(offer_links.readout) AS readout FROM offer_links JOIN readouts ON readouts.id = offer_links.readout
      LEFT JOIN readout_networks ON readout_networks.id = readouts.id
      WHERE readouts.key = ?1 AND readouts.offer = ?2 AND readouts.review != 'rejected' AND offer_links.sha = ?3
      GROUP BY offer_links.cloud, offer_links.path
      ORDER BY count(DISTINCT readout_networks.network) DESC, min(readouts.received) LIMIT ?4`)
      .bind(key, offer, sha, OFFER_FETCH_PATHS).all();
    let kept = false;
    for (const link of links) {
      const cloud = Object.hasOwn(CLOUDS, link.cloud) ? CLOUDS[link.cloud] : null;
      if (!cloud || !cloud.link.test(link.path)) {
        continue;
      }
      if (!(await reserve(env.DB, fetchesOn(day), day, { most: OFFER_FETCHES_DAILY }))) {
        done.capped = true;
        break;
      }
      const bytes = await download(fetcher, `${cloud.base}${link.path}?${cloud.query}`);
      if (!bytes || hex(await sha256(bytes)) !== sha) {
        continue;
      }
      if (!(await reserve(env.DB, READOUTS_EVER, null, { by: 0, bytes: bytes.byteLength, mostBytes: totalBytes }))) {
        done.capped = true;
        break;
      }
      await keepFile(env, link.readout, sha, bytes);
      kept = true;
      break;
    }
    (kept ? done.fetched : done.missing).push(sha);
  }
  return done;
}

// A file fetched, kept as its mark under the readout whose link fetched it:
// listed first, so a mark is never written that its readout does not list,
// and its bytes added to the readout's, so deleting it gives them back.
async function keepFile(env, readout, sha, bytes) {
  const object = await env.DB.prepare("SELECT object FROM readouts WHERE id = ?").bind(readout).first("object");
  await env.DB.batch([
    env.DB.prepare("INSERT OR IGNORE INTO readout_firmware (readout, sha) VALUES (?1, ?2)").bind(readout, sha),
    env.DB.prepare("UPDATE readouts SET bytes = bytes + ?2 WHERE id = ?1").bind(readout, bytes.byteLength),
  ]);
  try {
    await env.READOUTS.put(firmwareKey(sha), bytes, { customMetadata: { readout: object ?? "", fetched: "true" } });
  } catch (error) {
    await env.DB.batch([
      env.DB.prepare("DELETE FROM readout_firmware WHERE readout = ?1 AND sha = ?2").bind(readout, sha),
      env.DB.prepare("UPDATE readouts SET bytes = bytes - ?2 WHERE id = ?1").bind(readout, bytes.byteLength),
    ]);
    await release(env.DB, READOUTS_EVER, bytes.byteLength, 0);
    throw error;
  }
}

// The offers taken as right with a file linked and not held: of first stages
// taken as right, agreed (quorum) or approved, as only first stages carry links.
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
// each {key, offered, day}, the day it was taken as right.
export async function unfetched(db) {
  const { results } = await db.prepare(`SELECT agreed_offers.key, agreed_offers.offered, agreed_offers.day
    FROM agreed_offers JOIN (${UNFETCHED}) AS missing
      ON missing.key = agreed_offers.key AND missing.offer = agreed_offers.offer
    ORDER BY agreed_offers.day, agreed_offers.key`).all();
  return results;
}
