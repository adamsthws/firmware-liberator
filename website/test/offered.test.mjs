// /v1/offers (api/index.js offers) and api/offered.js: first stages sent as
// links, and whole readouts' links, are held and agreed as first stages are,
// and the API fetches their files itself, on first sight and once an offer is
// taken as right, keeping each only if it is the file its SHA-256 names; a
// link of a shape not seen before is kept for review, peeked at once and
// fetched only once approved. No test reaches eWeLink: every fetch is given
// one made here.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { read } from "../api/counters.js";
import worker, * as api from "../api/index.js";
import { d1 } from "../api/local-d1.mjs";
import { AFTER_ANSWER_MS, CLOUDS, FIRST_SIGHT_DAILY, FIRST_SIGHT_RENTED_DAILY, OFFER_FETCH_MAX_BYTES,
  OFFER_FETCH_PATHS, OFFER_FETCH_TIMEOUT_MS, OFFER_FETCHES_DAILY, OFFER_FIRST_SIGHT_PER_SENDER, OFFER_RETRY_DAYS,
  OFFER_RETRY_EACH_NIGHT, NAMES_KEPT_MOST, PEEK_BYTES, PENDING_LINK_DAYS, PENDING_LINKS_MOST, PENDING_LINKS_PER_SENDER,
  PENDING_LINKS_RENTED, REJECTED_LINKS_MOST, URL_MOST, URL_PATH_MOST, approveLink, fetchOffer, firmwareNames,
  firstSightsOn, isPublicAddress, kindOf, linkParts, linkPruning, linksNotKeptOn, linksToReview, namesKept, peekNamed,
  rejectLink, rentedFirstSightsOn, retryOffers } from "../api/offered.js";
import { PENDING, READOUTS_EVER, prunePending } from "../api/pending.js";
import { work } from "../api/proof-of-work.js";

// Nothing reaches the internet: a fetch no test gave fails.
globalThis.fetch = async () => {
  throw new Error("no network in the tests");
};

const NOW = new Date("2026-11-07T12:00:00Z");
const DAY = "2026-11-07";
const YESTERDAY = new Date("2026-11-06T12:00:00Z");
const TOMORROW = new Date("2026-11-08T12:00:00Z");
const BITS = 8;
work.bits = BITS;

const sha = (data) => createHash("sha256").update(data).digest("hex");
const APP = Buffer.from("the app eWeLink offers");
const TOKEN = "0123456789abcdef0123456789ABCDEF";
const PATH = `/ota/rom/${TOKEN}/user1.1024.new.2.bin`;
const URL_OF = (path) => `http://eu-otadl.coolkit.cc:8088${path}?deviceid=0&ts=1&sign=0`;
const FILE = { name: "user1.bin", version: "1.2.0", sha256: sha(APP), path: PATH };
const OFFER = { model: "NEWTHING", stock_model: "SN-NEW-01", stock_version: "1.0.0", device: null, files: [FILE] };
const KEY = "NEWTHING|SN-NEW-01|1.0.0";

function proofFor(body, time = NOW.getTime() / 1000) {
  const digest = sha(body);
  for (let nonce = 0; ; nonce += 1) {
    if (createHash("sha256").update(`${time}:${digest}:${nonce}`).digest().readUInt32BE(0) >>> (32 - BITS) === 0) {
      return `${time}:${nonce}`;
    }
  }
}

function bucket() {
  const objects = new Map();
  return {
    objects,
    head: async (key) => (objects.has(key) ? { key } : null),
    put: async (key, body, options) => objects.set(key, { body, options }),
    delete: async (keys) => { for (const key of [keys].flat()) objects.delete(key); },
  };
}

function mailer() {
  const sent = [];
  return { sent, send: async (message) => { sent.push(message); return { messageId: `m${sent.length}` }; } };
}

const env = (changes = {}) => ({ READOUTS: bucket(), DB: d1(), INGEST: "on", ENOUGH: "", SENDER_KEY: "a test key",
  OFFERED_SEND: "links", EMAIL: mailer(), READOUT_LIMIT: { limit: async () => ({ success: true }) }, ...changes });

// eWeLink's file server as a test makes it: each URL's bytes, every URL asked kept.
function cloud(files = { [URL_OF(PATH)]: APP }) {
  const asked = [];
  const fetcher = async (url) => {
    asked.push(String(url));
    const bytes = files[String(url)];
    return bytes ? new Response(bytes) : new Response("not found", { status: 404 });
  };
  return Object.assign(fetcher, { asked });
}

let nonce = 0;
// A request to /v1/offers from `address`, on network `asn`, with its work done.
function request(value, { address = "192.0.2.9", asn = 64496, headers = {}, body, now = NOW } = {}) {
  const bytes = body ?? Buffer.from(JSON.stringify({ format: 1, tool_version: "3.1.0", cloud: "ewelink",
    nonce: String((nonce += 1)).padStart(16, "0"), ...value }));
  const asking = new Request("https://api.firmwareliberator.com/v1/offers", { method: "POST", body: bytes,
    headers: { "Content-Type": "application/json", "Content-Length": String(bytes.byteLength),
      "CF-Connecting-IP": address, "X-Proof": proofFor(bytes, now.getTime() / 1000), ...headers } });
  return Object.defineProperty(asking, "cf", { value: { asn } });
}

// The offers sent, and the answer's results; `fetcher` stands for eWeLink.
async function send(bindings, offers, { fetcher = cloud(), now = NOW, ...options } = {}) {
  const response = await api.offers(request({ offers }, { ...options, now }), bindings, now, null, fetcher);
  return { status: response.status, ...(await response.json()) };
}
const results = async (...args) => (await send(...args)).offers.map((each) => each.result);

const rows = (bindings, sql, ...values) => bindings.DB.db.prepare(sql).all(...values).map((row) => ({ ...row }));

// Three senders, each on a network of its own: enough for an offer to agree.
const SENDERS = [["192.0.2.1", 64501], ["198.51.100.2", 64502], ["203.0.113.3", 64503]];
async function agree(bindings, offer = OFFER, fetcher = cloud(), senders = SENDERS, on = NOW) {
  for (const [index, [address, asn]] of senders.entries()) {
    const now = new Date(on.getTime() + index * 1000);
    assert.deepEqual(await results(bindings, [offer], { address, asn, fetcher, now }), ["taken"], address);
  }
  return fetcher;
}

// A file-bearing first stage, as /v1/readout takes it.
function tar(files) {
  const blocks = [];
  for (const [name, data] of Object.entries(files)) {
    const bytes = Buffer.from(data);
    const header = Buffer.alloc(512);
    header.write(name, 0);
    header.write(bytes.length.toString(8).padStart(11, "0"), 124);
    blocks.push(header, bytes, Buffer.alloc((512 - (bytes.length % 512)) % 512));
  }
  return Buffer.concat([...blocks, Buffer.alloc(1024)]);
}
// `linked`: its file sent as a link in place of the file (.github/readouts.md),
// with these entries' fields and the manifest's given.
async function readout(bindings, address, asn, { read = false, withFile = true, again = 0, linked = null } = {},
  fetcher = cloud()) {
  const offered = [{ file: withFile ? "offered/user1.bin" : null, version: "1.2.0", sha256: sha(APP),
    ...(linked ? { link: PATH, ...linked.entry } : {}) }];
  const manifest = { format: 1, model: "NEWTHING", stock_model: "SN-NEW-01", stock_version: "1.0.0", tool: "3.0.0",
    read, owner: address, again, offered, ...(linked ? { cloud: "ewelink", ...linked.manifest } : {}) };
  const body = tar({ "manifest.json": JSON.stringify(manifest), ...(withFile ? { "offered/user1.bin": APP } : {}),
    ...(read ? { "flash.bin.xz": XZ_1MIB } : {}) });
  const asking = new Request("https://api.firmwareliberator.com/v1/readout", { method: "POST", body, headers: {
    "Content-Type": "application/x-tar", "Content-Length": String(body.byteLength), "CF-Connecting-IP": address,
    "X-Proof": proofFor(body) } });
  Object.defineProperty(asking, "cf", { value: { asn } });
  return (await api.readout(asking, bindings, NOW, null, fetcher)).status;
}
// 1 MiB of zeros, xz-compressed as the tool's lzma.compress writes it: a whole readout's flash.
const XZ_1MIB = Buffer.from("/Td6WFoAAATm1rRGAgAhARYAAAB0L+Wj7///ANldAABv/f//o7f/Rz5IFXI5YVG4kijmo4YH+e7kHoLTL8U6PAFLsX7J"
  + "iopNL6MN2X+m44wjEVPgWRjFdYrid/i2lH8MasDedElk4ulcU7IE2PdEDKtfDW1G6eXDdoi3llestk3haR1v+0uIEGxCy4g/XACP0E6vJiiUcR89jyTh"
  + "cJ6nI1/sKMuF0ZWYin4qkfIndfcZwAaYTZj92K/VkA/EJVP49ZE2MQWlsO5vwXBNRwzRkRGqrWAdus6xJxhcWYbpZlJYvul2rFnk5VsFCPnH2q38+1Iq"
  + "9+SVJQAAAAAAwva6PqJwa2AAAfUBgIBAAHmXf0exxGf7AgAAAAAEWVo=", "base64");

test("an offer of a device eWeLink names no product model of is taken as \"-\"", async () => {
  assert.deepEqual(await send(env(), [{ ...OFFER, model: "-" }]), { status: 200, send: "links", offers: [{ result: "taken" }] });
});

test("a link whose token is 64 hex, as an NSPanel's, is taken", async () => {
  const path = `/ota/rom/${"0123456789abcdef".repeat(4)}/user1.1024.new.2.bin`;
  assert.deepEqual((await send(env(), [{ ...OFFER, files: [{ ...FILE, path }] }])).offers, [{ result: "taken" }]);
});

test("an offer sent as links is held as a first stage of its device is, its manifest and links kept", async () => {
  const bindings = env();
  assert.deepEqual(await send(bindings, [OFFER], { fetcher: cloud({}) }),
    { status: 200, send: "links", offers: [{ result: "taken" }] });
  const [row] = rows(bindings, "SELECT * FROM readouts");
  assert.equal(row.key, KEY, "the key a first stage of the device has: eWeLink's adds nothing");
  assert.equal(CLOUDS.ewelink.key, "");
  assert.deepEqual([row.stage, row.review, row.offered], ["first", "pending", "1.2.0"]);
  assert.equal(row.offer, await api.offerOf([sha(APP)]), "its offer as a file-bearing first stage's");
  assert.equal(row.fingerprint, row.offer);
  assert.match(row.id, /^2026-11-07-[0-9a-f]{12}$/);
  assert.match(row.object, /^readouts\/2026-11-07\/[0-9a-f]{24}\.json$/);
  const manifest = JSON.parse(bindings.READOUTS.objects.get(row.object).body);
  assert.deepEqual(manifest.offered, [{ name: "user1.bin", version: "1.2.0", sha256: sha(APP), link: PATH }]);
  assert.deepEqual([manifest.cloud, manifest.tool, manifest.read], ["ewelink", "3.1.0", false]);
  assert.equal(row.bytes, bindings.READOUTS.objects.get(row.object).body.length);
  assert.deepEqual(rows(bindings, "SELECT readout, sha, path, cloud, bytes FROM offer_links"),
    [{ readout: row.id, sha: sha(APP), path: PATH, cloud: "ewelink", bytes: null }], "its file not given");
  assert.deepEqual(await read(bindings.DB, READOUTS_EVER), { count: 1, bytes: row.bytes });
  assert.deepEqual(await read(bindings.DB, PENDING), { count: 1, bytes: row.bytes });
  assert.ok(!JSON.stringify(rows(bindings, "SELECT * FROM readouts")).includes("192.0.2.9"));
});

test("its sender's again, and one whose offer is taken as right, are answered have and count against nothing",
  async () => {
    const bindings = env();
    await send(bindings, [OFFER]);
    assert.deepEqual((await send(bindings, [OFFER])).offers, [{ result: "have", why: "held" }]);
    assert.equal((await read(bindings.DB, `offers:${DAY}`)).count, 1);
    await agree(bindings, OFFER, cloud(), SENDERS.slice(1));
    assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1);
    assert.deepEqual(await results(bindings, [OFFER], { address: "192.0.2.5", asn: 64505 }), ["have"]);
    assert.equal((await read(bindings.DB, `offers:${DAY}`)).count, 3);
  });

test("three from three networks agree, and each file is fetched, by name, and kept as one a readout holds", async () => {
  const bindings = { ...env(), ALERTS_RECIPIENT_EMAIL: "maintainer@example.com" };
  const fetcher = await agree(bindings);
  assert.deepEqual(fetcher.asked, [URL_OF(PATH)], "asked once, of the name every region answers to");
  assert.deepEqual(rows(bindings, "SELECT key, offered FROM agreed_offers"), [{ key: KEY, offered: "1.2.0" }]);
  assert.deepEqual(rows(bindings, "SELECT review FROM readouts").map((row) => row.review), ["quorum", "quorum", "quorum"]);
  const mark = bindings.READOUTS.objects.get(`readouts/firmware/${sha(APP)}`);
  assert.deepEqual(Buffer.from(mark.body), APP, "the file itself");
  const [{ readout }] = rows(bindings, "SELECT readout FROM readout_firmware WHERE sha = ?", sha(APP));
  const [holder] = rows(bindings, "SELECT object, bytes FROM readouts WHERE id = ?", readout);
  assert.equal(mark.options.customMetadata.readout, holder.object);
  assert.ok(holder.bytes > APP.length, "its bytes are its readout's");
  const manifests = [...bindings.READOUTS.objects.values()].filter((item) => typeof item.body === "string")
    .reduce((total, item) => total + item.body.length, 0);
  assert.deepEqual(await read(bindings.DB, READOUTS_EVER), { count: 3, bytes: manifests + APP.length });
  assert.deepEqual(await read(bindings.DB, PENDING), { count: 0, bytes: 0 }, "taken as right");
  assert.deepEqual(bindings.EMAIL.sent.map((message) => message.subject), [`Readouts agree: ${KEY} (first, offered 1.2.0)`]);
  // /v1/wanted names it held now, so no later first stage sends it.
  const done = await fetchOffer(bindings, KEY, await api.offerOf([sha(APP)]), NOW,
    { totalBytes: api.TOTAL_BYTES, fetcher });
  assert.deepEqual(done, { fetched: [], missing: [], capped: false });
  assert.equal(fetcher.asked.length, 1, "a file held is never fetched again");
});

test("links and file-bearing first stages agree on an offer together, each sender once", async () => {
  const bindings = env();
  assert.equal(await readout(bindings, "192.0.2.1", 64501), 201);
  assert.equal(await readout(bindings, "198.51.100.2", 64502), 201);
  assert.deepEqual(await results(bindings, [OFFER], { address: "192.0.2.1", asn: 64501 }), ["have"],
    "the same sender's first stage again, as links");
  const fetcher = cloud();
  assert.deepEqual(await results(bindings, [OFFER], { address: "203.0.113.3", asn: 64503, fetcher }), ["taken"]);
  assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1, "the third, as links, agrees with the two");
  assert.deepEqual(fetcher.asked, [], "the files held already, from the first stages that carried them");
});

test("a readout that takes an offer sent as links as right fetches its files", async () => {
  const bindings = env();
  // Not given on first sight, the day before.
  await send(bindings, [OFFER], { address: "192.0.2.1", asn: 64501, fetcher: cloud({}), now: YESTERDAY });
  await send(bindings, [OFFER], { address: "198.51.100.2", asn: 64502, fetcher: cloud({}), now: YESTERDAY });
  const fetcher = cloud();
  assert.equal(await readout(bindings, "203.0.113.3", 64503, { read: true, withFile: false }, fetcher), 201,
    "a whole readout naming the offer, its file left out");
  assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1);
  assert.deepEqual(fetcher.asked, [URL_OF(PATH)]);
  assert.ok(bindings.READOUTS.objects.has(`readouts/firmware/${sha(APP)}`));
});

test("a file that is not the one its SHA-256 names is dropped, its link never tried for it again", async () => {
  const bindings = env();
  const wrong = `/ota/rom/${"x".repeat(32)}/user1.bin`;
  const fetcher = cloud({ [URL_OF(wrong)]: Buffer.from("another file"), [URL_OF(PATH)]: APP });
  // Two senders' link fetches another file, on first sight, once; the third's the right one.
  await agree(bindings, { ...OFFER, files: [{ ...FILE, path: wrong }] }, fetcher, SENDERS.slice(0, 2));
  assert.deepEqual(fetcher.asked, [URL_OF(wrong)], "the second's same link not tried again");
  assert.deepEqual(rows(bindings, "SELECT sha, path, why, day FROM offer_fetch_failures"),
    [{ sha: sha(APP), path: wrong, why: "other", day: DAY }]);
  assert.ok(!bindings.READOUTS.objects.has(`readouts/firmware/${sha(APP)}`));
  await agree(bindings, OFFER, fetcher, SENDERS.slice(2));
  assert.deepEqual(fetcher.asked, [URL_OF(wrong), URL_OF(PATH)]);
  assert.deepEqual(rows(bindings, "SELECT * FROM offer_fetch_failures"), [], "forgotten once the file is held");
  assert.deepEqual(Buffer.from(bindings.READOUTS.objects.get(`readouts/firmware/${sha(APP)}`).body), APP);
  const [{ readout }] = rows(bindings, "SELECT readout FROM readout_firmware");
  assert.deepEqual(rows(bindings, "SELECT path FROM offer_links WHERE readout = ?", readout), [{ path: PATH }],
    "marked under the readout whose link fetched it");
});

test("links from one network, however many, are tried after those from more", async () => {
  const bindings = env();
  const wrong = `/ota/rom/${"x".repeat(32)}/user1.bin`;
  const fetcher = cloud({ [URL_OF(wrong)]: Buffer.from("another file"), [URL_OF(PATH)]: APP });
  const oneNetwork = [["198.51.100.11", 64510], ["198.51.100.12", 64510], ["198.51.100.13", 64510]];
  // Neither link gives anything on first sight, the day before.
  await agree(bindings, { ...OFFER, files: [{ ...FILE, path: wrong }] }, cloud({}), oneNetwork, YESTERDAY);
  assert.deepEqual(rows(bindings, "SELECT * FROM agreed_offers"), [], "from one network");
  await agree(bindings, OFFER, cloud({}), SENDERS.slice(0, 2), YESTERDAY);
  assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1);
  assert.equal((await retryOffers(bindings, NOW, { totalBytes: api.TOTAL_BYTES, fetcher })).fetched, 1);
  assert.deepEqual(fetcher.asked, [URL_OF(PATH)]);
});

test("no link giving the file agreed on keeps nothing, OFFER_FETCH_PATHS links tried at a time", async () => {
  assert.equal(OFFER_FETCH_PATHS, 3);
  const bindings = env();
  const paths = Array.from({ length: 3 }, (_, n) => `/ota/rom/${String(n).repeat(32)}/user1.bin`);
  const fetcher = cloud({ ...Object.fromEntries(paths.map((path) => [URL_OF(path), Buffer.from("not it")])),
    [URL_OF(PATH)]: APP });
  // Each gives nothing on first sight, the day before; then another file.
  for (const [index, path] of paths.entries()) {
    await agree(bindings, { ...OFFER, files: [{ ...FILE, path }] }, cloud({}), SENDERS.slice(index, index + 1),
      YESTERDAY);
  }
  assert.deepEqual(await fetchOffer(bindings, KEY, await api.offerOf([sha(APP)]), NOW,
    { totalBytes: api.TOTAL_BYTES, fetcher }), { fetched: [], missing: [sha(APP)], capped: false });
  assert.equal(fetcher.asked.length, OFFER_FETCH_PATHS);
  assert.deepEqual(rows(bindings, "SELECT * FROM readout_firmware"), []);
  assert.ok(![...bindings.READOUTS.objects.keys()].some((key) => key.startsWith("readouts/firmware/")));
  // A fourth link, the right one, given later: tried, as those that gave another file never are again.
  const [first] = rows(bindings, "SELECT * FROM readouts");
  bindings.DB.db.prepare(`INSERT INTO readouts (id, key, stage, sender, fingerprint, bytes, object, received, review,
    offer, offered) VALUES ('late', ?, 'first', 'late', ?, 1, 'x', '2026-11-08T00:00:00Z', 'quorum', ?, '1.2.0')`)
    .run(KEY, first.offer, first.offer);
  bindings.DB.db.prepare("INSERT INTO offer_links (readout, sha, path, cloud) VALUES ('late', ?, ?, 'ewelink')")
    .run(sha(APP), PATH);
  const done = await fetchOffer(bindings, KEY, first.offer, NOW, { totalBytes: api.TOTAL_BYTES, fetcher });
  assert.deepEqual(done, { fetched: [sha(APP)], missing: [], capped: false });
  assert.deepEqual(fetcher.asked.slice(OFFER_FETCH_PATHS), [URL_OF(PATH)]);
});

test("a file larger than any update file is not kept, said so or not", async () => {
  assert.equal(OFFER_FETCH_MAX_BYTES, 8 * 1024 * 1024, "as tools/ewelink/offered_firmware.py MAX_SIZE");
  const big = Buffer.alloc(OFFER_FETCH_MAX_BYTES + 1);
  for (const said of [true, false]) {
    const bindings = env();
    const fetcher = Object.assign(async (url) => {
      fetcher.asked.push(url);
      const body = said ? big : new ReadableStream({ start(controller) {
        controller.enqueue(big.subarray(0, 4096));
        controller.enqueue(big.subarray(4096));
        controller.close();
      } });
      return new Response(body, { headers: said ? { "Content-Length": String(big.length) } : {} });
    }, { asked: [] });
    await agree(bindings, { ...OFFER, files: [{ ...FILE, sha256: sha(big) }] }, fetcher);
    assert.equal(fetcher.asked.length, 1, String(said));
    assert.deepEqual(rows(bindings, "SELECT * FROM readout_firmware"), [], String(said));
  }
});

test("files are fetched OFFER_FETCHES_DAILY a day at most for offers taken as right, across everyone", async () => {
  assert.equal(OFFER_FETCHES_DAILY, 50);
  const bindings = env();
  bindings.DB.db.prepare("INSERT INTO counters (name, day, count) VALUES (?, ?, ?)")
    .run(`offer-fetches:${DAY}`, DAY, OFFER_FETCHES_DAILY - 1);
  // First sight's budget used up, so agreement alone fetches.
  bindings.DB.db.prepare("INSERT INTO counters (name, day, count) VALUES (?, ?, ?)")
    .run(firstSightsOn(DAY), DAY, FIRST_SIGHT_DAILY);
  const twoFiles = { ...OFFER, files: [FILE, { ...FILE, name: "user2.bin", sha256: sha("user2"),
    path: `/ota/rom/${TOKEN}/user2.bin` }] };
  const fetcher = cloud({ [URL_OF(PATH)]: APP, [URL_OF(`/ota/rom/${TOKEN}/user2.bin`)]: Buffer.from("user2") });
  await agree(bindings, twoFiles, fetcher);
  assert.equal(fetcher.asked.length, 1, "the day's last fetch, and no more");
  assert.equal((await read(bindings.DB, `offer-fetches:${DAY}`)).count, OFFER_FETCHES_DAILY);
  const tomorrow = new Date("2026-11-08T12:00:00Z");
  const done = await fetchOffer(bindings, KEY, await api.offerOf(twoFiles.files.map((file) => file.sha256)), tomorrow,
    { totalBytes: api.TOTAL_BYTES, fetcher });
  assert.equal(done.fetched.length, 1, "the next day, the file left");
  assert.equal(fetcher.asked.length, 2, "the file held is not fetched again");
});

test("a file is fetched within the store's limit", async () => {
  const bindings = env();
  await agree(bindings, OFFER, cloud({}));
  bindings.DB.db.prepare("UPDATE counters SET bytes = ? WHERE name = ?").run(api.TOTAL_BYTES - 1, READOUTS_EVER);
  const done = await fetchOffer(bindings, KEY, await api.offerOf([sha(APP)]), TOMORROW,
    { totalBytes: api.TOTAL_BYTES, fetcher: cloud() });
  assert.deepEqual(done, { fetched: [], missing: [sha(APP)], capped: true });
  assert.deepEqual(rows(bindings, "SELECT * FROM readout_firmware"), []);
  assert.equal((await read(bindings.DB, READOUTS_EVER)).bytes, api.TOTAL_BYTES - 1);
});

test("anything but the tool's request is turned away whole", async () => {
  const bindings = env();
  const asked = async (value, options) => (await api.offers(request(value, options), bindings, NOW, null, cloud()))
    .status;
  assert.equal(await asked({ offers: [OFFER] }), 200);
  for (const value of [{ cloud: "tuya", offers: [OFFER] }, { cloud: "constructor", offers: [OFFER] },
    { cloud: undefined, offers: [OFFER] }, { offers: Array(api.OFFERS_PER_REQUEST + 1).fill(OFFER) },
    { offers: "all" }, { format: 2, offers: [OFFER] }, { tool_version: "3.1\n", offers: [OFFER] }]) {
    assert.equal(await asked(value), 400, JSON.stringify(value).slice(0, 80));
  }
  assert.equal(await asked({}, { body: Buffer.from("not json") }), 400);
  assert.equal(await asked({ offers: [OFFER] }, { headers: { "Content-Type": "text/plain" } }), 415);
  assert.equal(await asked({ offers: [OFFER] }, { headers: { "X-Proof": "1:1" } }), 403);
  assert.equal(await asked({}, { body: Buffer.alloc(api.OFFERS_MAX_BYTES + 1, 32) }), 413);
  assert.equal(api.OFFERS_MAX_BYTES, 32 * 1024);
  assert.ok(api.OFFERS_MAX_BYTES > api.QUESTION_MAX_BYTES, "its own limit, not a question's");
  const fifty = Array.from({ length: api.OFFERS_PER_REQUEST }, (_, n) => ({ ...OFFER, stock_version: `1.0.${n}`,
    device: sha(`device ${n}`), files: [FILE, { ...FILE, name: "user2.bin", sha256: sha(`2 ${n}`) }] }));
  assert.equal(await asked({ offers: fifty }), 200, "an account's fifty devices fit");
  assert.equal((await api.offers(request({ offers: [OFFER] }), env({ SENDER_KEY: undefined }), NOW)).status, 503);
});

test("each offer not as the tool sends one is refused, and the rest taken, answered in order", async () => {
  const bindings = env();
  const offers = [
    { ...OFFER, model: "NEW|THING" },
    { ...OFFER, stock_version: "1.0 beta" },
    { ...OFFER, files: "user1.bin" },
    { ...OFFER, files: Array.from({ length: api.MAX_FIRMWARE + 1 }, (_, n) => ({ ...FILE, sha256: sha(String(n)) })) },
    { ...OFFER, files: [{ ...FILE, sha256: "A".repeat(64) }] },
    { ...OFFER, files: [{ ...FILE, version: undefined }] },
    { ...OFFER, files: [{ ...FILE, name: "" }] },
    { ...OFFER, files: [{ ...FILE, path: `http://192.0.2.1:8088${PATH}` }] },
    { ...OFFER, files: [{ ...FILE, path: "/ota/rom/short/user1.bin" }] },
    { ...OFFER, files: [{ ...FILE, path: `/ota/rom/${"a".repeat(65)}/user1.bin` }] },
    { ...OFFER, files: [{ ...FILE, path: `${PATH}?deviceid=1` }] },
    { ...OFFER, files: [{ ...FILE, path: `/ota/rom/${TOKEN}/../../admin` }] },
    { ...OFFER, files: [FILE, { ...FILE, name: "again.bin" }] },
    null,
    OFFER,
  ];
  const answered = (await send(bindings, offers)).offers;
  assert.deepEqual(answered.map((each) => each.result), [...Array(offers.length - 1).fill("refused"), "taken"]);
  assert.ok(answered.slice(0, -1).every((each) => typeof each.why === "string" && each.why), "each says why");
  assert.equal(rows(bindings, "SELECT * FROM readouts").length, 1);
});

test("an offer of nothing, or of a device enough readouts of are in, is answered have and kept nowhere", async () => {
  const bindings = env({ ENOUGH: "OLDTHING" });
  const answered = await send(bindings, [{ ...OFFER, files: [] }, { ...OFFER, model: "oldthing" }]);
  assert.deepEqual(answered.offers, [{ result: "have", why: "offered nothing" }, { result: "have", why: "enough" }]);
  assert.deepEqual(rows(bindings, "SELECT * FROM readouts"), []);
  assert.equal(bindings.READOUTS.objects.size, 0);
  assert.equal((await read(bindings.DB, `offers:${DAY}`)).count, 0);
});

// `n` offers, each of a device of its own.
const devices = (n, from = 0) => Array.from({ length: n }, (_, at) => ({ ...OFFER, stock_version: `2.0.${from + at}` }));

test("SENDER_OFFERS_DAILY offers a day are kept from one sender, the rest answered full", async () => {
  assert.equal(api.SENDER_OFFERS_DAILY, 20);
  const bindings = env();
  assert.deepEqual(new Set(await results(bindings, devices(15))), new Set(["taken"]));
  assert.deepEqual(await results(bindings, devices(7, 15)), [...Array(5).fill("taken"), "full", "full"],
    "one request taken up to the limit, the rest full");
  assert.deepEqual(await results(bindings, devices(1, 20)), ["full"]);
  assert.deepEqual(await results(bindings, devices(1, 20), { address: "192.0.2.77" }), ["taken"], "another sender's");
  assert.deepEqual(await results(bindings, devices(1, 21), { now: new Date("2026-11-08T12:00:00Z") }), ["taken"],
    "and the next day");
  assert.deepEqual(await results(bindings, devices(1, 0)), ["have"], "one held answers have, past the limit too");
});

test("OFFERS_DAILY offers a day are kept from everyone, a sender's place given back past it", async () => {
  assert.equal(api.OFFERS_DAILY, 2000);
  const bindings = env();
  bindings.DB.db.prepare("INSERT INTO counters (name, day, count) VALUES (?, ?, ?)")
    .run(`offers:${DAY}`, DAY, api.OFFERS_DAILY - 1);
  assert.deepEqual(await results(bindings, devices(3)), ["taken", "full", "full"]);
  const counted = rows(bindings, "SELECT count FROM counters WHERE name GLOB 'offer-sender:*'");
  assert.deepEqual(counted, [{ count: 1 }], "the sender's count is of the one kept");
});

test("offers from a network that counts as none take RENTED_SHARE of OFFERS_DAILY, the rest kept for owners", async () => {
  const share = (api.OFFERS_DAILY * api.RENTED_SHARE) / 100;
  const bindings = env();
  bindings.DB.db.prepare("INSERT INTO counters (name, day, count) VALUES (?, ?, ?)")
    .run(`rented-offers:${DAY}`, DAY, share - 1);
  assert.deepEqual(await results(bindings, devices(3), { asn: 16509 }), ["taken", "full", "full"]);
  assert.equal((await read(bindings.DB, `offers:${DAY}`)).count, 1, "everyone's day holds only the one kept");
  assert.deepEqual(rows(bindings, "SELECT count FROM counters WHERE name GLOB 'offer-sender:*'"), [{ count: 1 }],
    "and the sender's");
  assert.deepEqual(await results(bindings, devices(2, 3), { address: "192.0.2.77" }), ["taken", "taken"],
    "an owner's network's");
  assert.equal((await read(bindings.DB, `rented-offers:${DAY}`)).count, share, "counted nowhere in the share");
});

test("past the store's limits offers are answered full, and their places given back", async () => {
  for (const [name, most] of [[READOUTS_EVER, api.TOTAL_BYTES], [PENDING, api.PENDING_BYTES]]) {
    const bindings = env();
    bindings.DB.db.prepare("INSERT INTO counters (name, day, count, bytes) VALUES (?, NULL, 0, ?)").run(name, most);
    assert.deepEqual(await results(bindings, [OFFER]), ["full"], name);
    assert.equal((await read(bindings.DB, `offers:${DAY}`)).count, 0, name);
    assert.deepEqual(rows(bindings, "SELECT * FROM readouts"), [], name);
  }
});

test("through the Worker, the files are fetched after the answer, waited on", async () => {
  const bindings = env();
  await agree(bindings, OFFER, cloud({}), SENDERS.slice(0, 2));
  const waited = [];
  const fetcher = cloud();
  const real = globalThis.fetch;
  globalThis.fetch = fetcher;
  try {
    const now = Date.now() / 1000;
    const body = Buffer.from(JSON.stringify({ format: 1, cloud: "ewelink", offers: [OFFER], nonce: "x" }));
    const asking = new Request("https://api.firmwareliberator.com/v1/offers", { method: "POST", body, headers: {
      "Content-Type": "application/json", "Content-Length": String(body.length), "CF-Connecting-IP": "203.0.113.3",
      "X-Proof": proofFor(body, Math.floor(now)) } });
    Object.defineProperty(asking, "cf", { value: { asn: 64503 } });
    const response = await worker.fetch(asking, bindings, { waitUntil: (promise) => waited.push(promise) });
    assert.deepEqual(await response.json(), { send: "links", offers: [{ result: "taken" }] });
    assert.equal(waited.length, 1);
    await Promise.all(waited);
  } finally {
    globalThis.fetch = real;
  }
  assert.deepEqual(fetcher.asked, [URL_OF(PATH)]);
  assert.ok(bindings.READOUTS.objects.has(`readouts/firmware/${sha(APP)}`));
});

test("a fetch that fails, or a cloud's server that answers no file, keeps nothing and stops nothing", async () => {
  const bindings = env();
  const failing = Object.assign(async (url) => {
    failing.asked.push(url);
    throw new Error("connection refused");
  }, { asked: [] });
  await agree(bindings, OFFER, failing);
  assert.equal(failing.asked.length, 1);
  assert.deepEqual(rows(bindings, "SELECT * FROM readout_firmware"), []);
  assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1, "the offer is agreed all the same");
});

// Every count and object an answer could leave behind.
const traces = (bindings) => ({ readouts: rows(bindings, "SELECT * FROM readouts"),
  counters: rows(bindings, "SELECT name FROM counters WHERE name NOT GLOB 'proof:*'"),
  objects: [...bindings.READOUTS.objects.keys()] });
const NOTHING = { readouts: [], counters: [], objects: [] };

test("no offers at all asks only what to send: answered, counting and keeping nothing", async () => {
  for (const setting of ["links", "files", undefined]) {
    const bindings = env({ OFFERED_SEND: setting });
    assert.deepEqual(await send(bindings, []), { status: 200, send: setting ?? "files", offers: [] }, String(setting));
    assert.deepEqual(traces(bindings), NOTHING, String(setting));
    assert.deepEqual(rows(bindings, "SELECT * FROM wanted_days"), [], "not a question in the figures");
  }
  const unworked = await api.offers(request({ offers: [] }, { headers: { "X-Proof": "1:1" } }), env(), NOW);
  assert.equal(unworked.status, 403, "the work is asked all the same");
});

test("while the API takes files, offers are answered full and nothing is kept", async () => {
  const bindings = env({ OFFERED_SEND: "files" });
  const fetcher = cloud();
  const answered = await send(bindings, [OFFER, { ...OFFER, model: "a|b" }], { fetcher });
  assert.equal(answered.send, "files");
  assert.deepEqual(answered.offers.map((each) => each.result), ["full", "refused"]);
  assert.equal(answered.offers[0].why, "the API takes files, not links, for now");
  assert.deepEqual(traces(bindings), NOTHING);
  assert.deepEqual(fetcher.asked, []);
});

// /v1/wanted asked by `address` of the device and offer, at `stage`.
async function wanted(bindings, address, asn, stage) {
  const body = Buffer.from(JSON.stringify({ format: 1, model: "NEWTHING", stock_model: "SN-NEW-01",
    stock_version: "1.0.0", stage, firmware: [sha(APP)], nonce: `${address}-${stage}-${(nonce += 1)}` }));
  const asking = new Request("https://api.firmwareliberator.com/v1/wanted", { method: "POST", body, headers: {
    "Content-Type": "application/json", "Content-Length": String(body.length), "CF-Connecting-IP": address,
    "X-Proof": proofFor(body) } });
  Object.defineProperty(asking, "cf", { value: { asn } });
  return (await api.wanted(asking, bindings, NOW)).json();
}

test("a sender's whole readout after its own first stage of the same offer, as links or files, is wanted and held",
  async () => {
    for (const asLinks of [true, false]) {
      const bindings = env();
      if (asLinks) {
        assert.deepEqual(await results(bindings, [OFFER], { address: "192.0.2.1", asn: 64501 }), ["taken"]);
      } else {
        assert.equal(await readout(bindings, "192.0.2.1", 64501), 201);
      }
      assert.equal((await wanted(bindings, "192.0.2.1", 64501, "whole")).wanted, true, String(asLinks));
      assert.equal(await readout(bindings, "192.0.2.1", 64501, { read: true }), 201, String(asLinks));
      assert.deepEqual(rows(bindings, "SELECT stage, offer = '' AS none FROM readouts ORDER BY stage"),
        [{ stage: "first", none: 0 }, { stage: "whole", none: 1 }], "the whole one names no offer again");
    }
  });

test("one sender's first stage and whole readout count once towards their offer", async () => {
  const bindings = env();
  await send(bindings, [OFFER], { address: "192.0.2.1", asn: 64501 });
  await readout(bindings, "192.0.2.1", 64501, { read: true });
  await send(bindings, [OFFER], { address: "198.51.100.2", asn: 64502 });
  assert.deepEqual(rows(bindings, "SELECT * FROM agreed_offers"), [], "two senders, not three");
  await send(bindings, [OFFER], { address: "203.0.113.3", asn: 64503 });
  assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1);
});

test("a sender's whole readout twice, or first stage twice, as links or files, is still one", async () => {
  const bindings = env();
  assert.equal(await readout(bindings, "192.0.2.1", 64501, { read: true }), 201);
  assert.equal((await wanted(bindings, "192.0.2.1", 64501, "whole")).why, "held");
  assert.equal(await readout(bindings, "192.0.2.1", 64501, { read: true, again: 1 }), 200, "a second whole one");
  assert.deepEqual(await results(bindings, [OFFER], { address: "192.0.2.1", asn: 64501 }), ["have"],
    "a first stage after its whole readout of the offer");
  const firsts = env();
  assert.equal(await readout(firsts, "192.0.2.1", 64501), 201);
  assert.equal(await readout(firsts, "192.0.2.1", 64501, { again: 1 }), 200, "files, again");
  assert.deepEqual(await results(firsts, [OFFER], { address: "192.0.2.1", asn: 64501 }), ["have"], "then as links");
  assert.deepEqual(await results(firsts, [OFFER], { address: "198.51.100.2", asn: 64502 }), ["taken"]);
  assert.deepEqual(await results(firsts, [OFFER], { address: "198.51.100.2", asn: 64502 }), ["have"], "links, again");
  assert.equal(rows(firsts, "SELECT * FROM readouts").length, 2);
});

test("an offer's file is fetched on first sight, held before its offer is taken as right", async () => {
  const bindings = env();
  const fetcher = cloud();
  assert.deepEqual(await results(bindings, [OFFER], { fetcher }), ["taken"]);
  assert.deepEqual(fetcher.asked, [URL_OF(PATH)]);
  assert.deepEqual(rows(bindings, "SELECT * FROM agreed_offers"), [], "one sender agrees nothing");
  const [row] = rows(bindings, "SELECT id, object, bytes, review FROM readouts");
  assert.equal(row.review, "pending", "its offer's place as before");
  assert.deepEqual(rows(bindings, "SELECT readout FROM readout_firmware"), [{ readout: row.id }]);
  assert.deepEqual(rows(bindings, "SELECT bytes FROM offer_links"), [{ bytes: APP.length }]);
  const mark = bindings.READOUTS.objects.get(`readouts/firmware/${sha(APP)}`);
  assert.deepEqual(Buffer.from(mark.body), APP);
  assert.deepEqual(mark.options.customMetadata, { readout: row.object, fetched: "true" }, "no wrapper, no names");
  assert.equal((await read(bindings.DB, READOUTS_EVER)).bytes, row.bytes, "the file's bytes its readout's");
  // Another sender's offer of it fetches nothing: it is held.
  assert.deepEqual(await results(bindings, [OFFER], { address: "198.51.100.2", asn: 64502, fetcher }), ["taken"]);
  assert.equal(fetcher.asked.length, 1);
});

test("a link that gave nothing is not tried again on first sight, but is once a day for its offer taken as right",
  async () => {
    const bindings = env();
    const nothing = cloud({});
    await agree(bindings, OFFER, nothing, SENDERS.slice(0, 2), YESTERDAY);
    assert.deepEqual(nothing.asked, [URL_OF(PATH)], "the second sender's same link not tried");
    assert.deepEqual(rows(bindings, "SELECT why, day FROM offer_fetch_failures"), [{ why: "none", day: "2026-11-06" }]);
    // The third takes it as right today: its link failed yesterday, so it is tried, and gives nothing again.
    await agree(bindings, OFFER, nothing, SENDERS.slice(2));
    assert.equal(nothing.asked.length, 2);
    assert.equal((await retryOffers(bindings, NOW, { totalBytes: api.TOTAL_BYTES, fetcher: nothing })).fetched, 0);
    assert.equal(nothing.asked.length, 2, "not again the same day");
    const fetcher = cloud();
    assert.equal((await retryOffers(bindings, TOMORROW, { totalBytes: api.TOTAL_BYTES, fetcher })).fetched, 1,
      "the night after");
    assert.deepEqual(fetcher.asked, [URL_OF(PATH)]);
  });

test("a link that gave another file is never tried again for that SHA-256, nightly too, but is for another",
  async () => {
    const bindings = env();
    const other = Buffer.from("another release");
    const fetcher = cloud({ [URL_OF(PATH)]: other });
    await agree(bindings, OFFER, fetcher);
    assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1);
    assert.deepEqual(fetcher.asked, [URL_OF(PATH)]);
    await retryOffers(bindings, TOMORROW, { totalBytes: api.TOTAL_BYTES, fetcher });
    assert.equal(fetcher.asked.length, 1);
    const theirs = { ...OFFER, stock_version: "1.0.1", files: [{ ...FILE, sha256: sha(other) }] };
    assert.deepEqual(await results(bindings, [theirs], { fetcher }), ["taken"]);
    assert.equal(fetcher.asked.length, 2, "the same link, for the file it gives");
    assert.ok(bindings.READOUTS.objects.has(`readouts/firmware/${sha(other)}`));
  });

test("first sight fetches within FIRST_SIGHT_DAILY, and notes no failure past it", async () => {
  assert.equal(FIRST_SIGHT_DAILY, 50);
  const bindings = env();
  bindings.DB.db.prepare("INSERT INTO counters (name, day, count) VALUES (?, ?, ?)")
    .run(firstSightsOn(DAY), DAY, FIRST_SIGHT_DAILY);
  const fetcher = cloud();
  assert.deepEqual(await results(bindings, [OFFER], { fetcher }), ["taken"]);
  assert.deepEqual(fetcher.asked, []);
  assert.deepEqual(rows(bindings, "SELECT * FROM offer_fetch_failures"), []);
  assert.deepEqual(await results(bindings, [OFFER], { address: "198.51.100.2", fetcher, now: TOMORROW }), ["taken"]);
  assert.deepEqual(fetcher.asked, [URL_OF(PATH)], "the next day's offer of it, as its link was never tried");
});

// An update file's first bytes: Sonoff's wrapper naming `names`, after a Beken
// file's outer header of `outer` bytes if it is given.
function wrapped(names, outer = null) {
  const wrapper = Buffer.alloc(0x18 + names.length * 76 + 64);
  wrapper[0] = 0x03;
  wrapper[1] = names.length;
  names.forEach((name, index) => wrapper.write(name, 0x18 + index * 76, "ascii"));
  if (outer === null) {
    return wrapper;
  }
  const header = Buffer.alloc(16 + outer);
  header.writeUInt32LE(0x1beef11e, 0);
  header.writeUInt16LE(outer, 12);
  return Buffer.concat([header, wrapper]);
}

test("a file's firmware names are read from its wrapper, as the tool reads them; an ESP8266 file has none", () => {
  assert.deepEqual(firmwareNames(wrapped(["FWSW-E32S60-S60-ESP32C3FN4"])), ["FWSW-E32S60-S60-ESP32C3FN4"]);
  const panel = ["FWPNL-E32NSP-NSPANEL-ESP32D0WDV3", "FWPNL-NSP-HMI", "FWPNL-NSP-BLE"];
  assert.deepEqual(firmwareNames(wrapped(panel)), panel);
  assert.deepEqual(firmwareNames(wrapped(["FWSW-BK-S61-BK7238"], 4)), ["FWSW-BK-S61-BK7238"], "a Beken file");
  const esp8266 = Buffer.concat([Buffer.from([0xea, 0x04, 0x00, 0x20]), Buffer.alloc(200)]);
  assert.deepEqual(firmwareNames(esp8266), []);
  assert.deepEqual(firmwareNames(Buffer.alloc(1)), [], "too short");
  assert.deepEqual(firmwareNames(Buffer.from("plain text, no wrapper at all, long enough to reach 0x18 and on")), []);
});

test("a fetched file keeps its wrapper's names for review, and one with none is kept all the same", async () => {
  const s60 = Buffer.concat([wrapped(["FWSW-E32S60-S60-ESP32C3FN4"]), Buffer.from("the app")]);
  const esp8266 = Buffer.concat([Buffer.from([0xea, 0x04, 0x00, 0x20]), Buffer.alloc(200, 1)]);
  const bindings = env();
  const paths = [`/ota/rom/${TOKEN}/user1.bin`, `/ota/rom/${TOKEN}/user2.bin`];
  const fetcher = cloud({ [URL_OF(paths[0])]: s60, [URL_OF(paths[1])]: esp8266 });
  const offer = { ...OFFER, files: [s60, esp8266].map((file, at) => ({ ...FILE, name: `user${at + 1}.bin`,
    sha256: sha(file), path: paths[at] })) };
  assert.deepEqual(await results(bindings, [offer], { fetcher }), ["taken"]);
  const metadata = (file) => bindings.READOUTS.objects.get(`readouts/firmware/${sha(file)}`).options.customMetadata;
  assert.equal(metadata(s60).names, "FWSW-E32S60-S60-ESP32C3FN4");
  assert.ok(!("names" in metadata(esp8266)));
  assert.equal(rows(bindings, "SELECT * FROM readout_firmware").length, 2);
});

test("a whole readout's links are checked, kept and fetched on first sight, its offer as with the file", async () => {
  const bindings = env();
  const fetcher = cloud();
  assert.equal(await readout(bindings, "192.0.2.1", 64501, { read: true, withFile: false, linked: {} }, fetcher), 201);
  assert.deepEqual(fetcher.asked, [URL_OF(PATH)]);
  const [row] = rows(bindings, "SELECT id, object, offer, stage FROM readouts");
  assert.equal(row.stage, "whole");
  assert.equal(row.offer, await api.offerOf([sha(APP)]), "its offer by its SHA-256s, as before");
  assert.deepEqual(rows(bindings, "SELECT readout, sha, path, cloud, bytes FROM offer_links"),
    [{ readout: row.id, sha: sha(APP), path: PATH, cloud: "ewelink", bytes: APP.length }]);
  assert.deepEqual(rows(bindings, "SELECT readout FROM readout_firmware"), [{ readout: row.id }]);
  assert.equal(bindings.READOUTS.objects.get(`readouts/firmware/${sha(APP)}`).options.customMetadata.readout,
    row.object);
  // With its offer's links from two more networks, it takes the offer as right; the file is held already.
  await agree(bindings, OFFER, fetcher, SENDERS.slice(1));
  assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1);
  assert.equal(fetcher.asked.length, 1);
  // A first stage carrying links in place of the file, as its manifest says, is taken too.
  const firsts = env();
  assert.equal(await readout(firsts, "192.0.2.1", 64501, { withFile: false, linked: { manifest: { cloud: undefined } } },
    cloud()), 201, "the cloud implied");
  assert.equal(rows(firsts, "SELECT * FROM offer_links").length, 1);
});

test("a readout whose link is not as eWeLink gives one, or of another cloud, is turned away", async () => {
  for (const linked of [{ entry: { link: `http://192.0.2.1:8088${PATH}` } }, { entry: { link: "/ota/rom/short/x" } },
    { entry: { link: 5 } }, { entry: { sha256: "A".repeat(64) } }, { manifest: { cloud: "tuya" } }]) {
    const bindings = env();
    const fetcher = cloud();
    assert.equal(await readout(bindings, "192.0.2.1", 64501, { read: true, withFile: false, linked }, fetcher), 400,
      JSON.stringify(linked));
    assert.deepEqual(traces(bindings), NOTHING, JSON.stringify(linked));
    assert.deepEqual(fetcher.asked, []);
  }
});

test("one sender's first-sight fetches are OFFER_FIRST_SIGHT_PER_SENDER a day; agreement's are not held to it",
  async () => {
    assert.equal(OFFER_FIRST_SIGHT_PER_SENDER, 5);
    const bindings = env();
    // Six offers of six devices, each its own file at its own link.
    const fileOf = (n) => Buffer.from(`release ${n}`);
    const pathOf = (n) => `/ota/rom/${TOKEN}/user${n}.bin`;
    const offerOf = (n) => ({ ...OFFER, stock_version: `3.0.${n}`,
      files: [{ ...FILE, sha256: sha(fileOf(n)), path: pathOf(n) }] });
    const fetcher = cloud(Object.fromEntries([0, 1, 2, 3, 4, 5].map((n) => [URL_OF(pathOf(n)), fileOf(n)])));
    const six = [0, 1, 2, 3, 4, 5].map(offerOf);
    assert.deepEqual(await results(bindings, six, { address: "192.0.2.1", asn: 64501, fetcher }), Array(6).fill("taken"));
    assert.equal(fetcher.asked.length, OFFER_FIRST_SIGHT_PER_SENDER, "the sixth not fetched");
    assert.ok(!bindings.READOUTS.objects.has(`readouts/firmware/${sha(fileOf(5))}`));
    assert.deepEqual(rows(bindings, "SELECT * FROM offer_fetch_failures"), [], "and no failure");
    // Another sender's offer of it is fetched on first sight.
    const other = env();
    const theirs = cloud({ [URL_OF(pathOf(5))]: fileOf(5) });
    await results(other, six, { address: "192.0.2.1", asn: 64501, fetcher: cloud({}) });
    assert.deepEqual(await results(other, [six[5]], { address: "198.51.100.2", asn: 64502, fetcher: theirs }), ["taken"]);
    assert.ok(other.READOUTS.objects.has(`readouts/firmware/${sha(fileOf(5))}`));
    // Agreement fetches it, though every sender of it has used its first sights.
    for (const [at, [address, asn]] of SENDERS.slice(1).entries()) {
      const five = [0, 1, 2, 3, 4].map((n) => offerOf(10 * (at + 1) + n));
      const used = cloud({});
      await results(bindings, five, { address, asn, fetcher: used });
      assert.equal(used.asked.length, OFFER_FIRST_SIGHT_PER_SENDER, address);
    }
    const agreeing = cloud({ [URL_OF(pathOf(5))]: fileOf(5) });
    await agree(bindings, six[5], agreeing, SENDERS.slice(1));
    assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1);
    assert.deepEqual(agreeing.asked, [URL_OF(pathOf(5))], "once, by agreement");
    assert.ok(bindings.READOUTS.objects.has(`readouts/firmware/${sha(fileOf(5))}`));
  });

// Links of a shape not seen before (`url`): kept for review, peeked at once,
// never fetched until approved. A public address that is made up for the
// tests; a request never reaches it.
const ADDRESS = "1.2.3.4";
const URL_PATH = "/ota/s61/bk7238/user_ota.rbl";
const BY_ADDRESS = `http://${ADDRESS}:8088${URL_PATH}`;
const BY_NAME = `https://global-otadl2bsy.coolkit.cc${URL_PATH}`;
// A link by eWeLink's name as it is asked for: as sent, with the query its servers want.
const BY_NAME_ASKED = `${BY_NAME}?deviceid=0&ts=1&sign=0`;
const URL_FILE = { name: "user_ota.rbl", version: "1.2.0", sha256: sha(APP), url: BY_ADDRESS };
const URL_OFFER = { ...OFFER, files: [URL_FILE] };
const SERVER_URL = (server, path = URL_PATH) => `http://${server}:8088${path}?deviceid=0&ts=1&sign=0`;

// A server a test makes: `answer(url, init)` gives each response, every
// request kept with what it asked.
function server(answer = () => new Response("not found", { status: 404 })) {
  const asked = [];
  const fetcher = async (url, init = {}) => {
    asked.push({ url: String(url), range: new Headers(init.headers).get("Range"), redirect: init.redirect ?? null });
    return answer(String(url), init);
  };
  return Object.assign(fetcher, { asked });
}
const linkRows = (bindings) => rows(bindings, "SELECT sha, path, scheme, host, port, review FROM offer_links ORDER BY readout");
const peeks = (bindings) => rows(bindings, "SELECT * FROM link_peeks ORDER BY link");

test("a link to review keeps every rule, or is refused", () => {
  assert.deepEqual(linkParts(BY_ADDRESS), { scheme: "http", host: ADDRESS, port: 8088, path: URL_PATH });
  assert.deepEqual(linkParts("HTTPS://EU-OTADL.coolkit.CC/a_b-c.d/e"),
    null, "the scheme in lower case, as the tool sends it");
  assert.deepEqual(linkParts("https://EU-OTADL.coolkit.CC/a_b-c.d/e"),
    { scheme: "https", host: "eu-otadl.coolkit.cc", port: null, path: "/a_b-c.d/e" });
  for (const good of ["http://cn-otadl.coolkit.cn/x", "http://a.b.coolkit.cc:80/x", "https://1.2.3.4:443/",
    `http://x.coolkit.cc/${"a".repeat(URL_PATH_MOST - 1)}`]) {
    assert.notEqual(linkParts(good), null, good);
  }
  const refused = [
    // scheme
    "ftp://x.coolkit.cc/a", "file:///etc/passwd", "javascript:alert(1)", "//x.coolkit.cc/a", "x.coolkit.cc/a",
    // host: not eWeLink's name, nor a public IPv4 address
    "http://coolkit.cc/a", "http://evil.com/a", "http://x.coolkit.cc.evil.com/a", "http://xcoolkit.cc/a",
    "http://x.coolkit.cc./a", "http://-x.coolkit.cc/a", "http://x..coolkit.cc/a", "http://localhost/a",
    "http://user@x.coolkit.cc/a", "http://user:pw@1.2.3.4/a", "http://[::1]/a", "http://[2001:db8::1]:8088/a",
    "http://01.2.3.4/a", "http://1.2.3/a", "http://1.2.3.4.5/a", "http://256.2.3.4/a", "http://0x01020304/a",
    "http://16909060/a",
    // ports
    "http://x.coolkit.cc:8080/a", "http://x.coolkit.cc:088/a", "http://x.coolkit.cc:/a", "http://x.coolkit.cc:99999/a",
    // path
    "http://x.coolkit.cc", "http://x.coolkit.cc/a/../b", "http://x.coolkit.cc/..", "http://x.coolkit.cc//a",
    "http://x.coolkit.cc/a//b", "http://x.coolkit.cc/a%2e%2e/b", "http://x.coolkit.cc/%2F", "http://x.coolkit.cc/a b",
    `http://x.coolkit.cc/${"a".repeat(URL_PATH_MOST)}`, `http://x.coolkit.cc/${"a".repeat(URL_MOST)}`,
    // query, fragment
    "http://x.coolkit.cc/a?deviceid=1", "http://x.coolkit.cc/a#b", "http://x.coolkit.cc/a?",
    // what an injection would look like
    "http://x.coolkit.cc/a'b", 'http://x.coolkit.cc/a"b', "http://x.coolkit.cc/<script>", "http://x.coolkit.cc/a>b",
    "http://x.coolkit.cc/a\nb", "http://x.coolkit.cc/a\r\nSet-Cookie:x", "http://x.coolkit.cc/a\tb",
    "http://x.coolkit.cc/a`id`", "http://x.coolkit.cc/$(id)", "http://x.coolkit.cc/a;rm", "http://x.coolkit.cc/a|b",
    "http://x.coolkit.cc/a&b", "http://x.coolkit.cc/a\\b", "http://x.coolkit.cc/a\u0000b", "http://x.coolkit.cc/é",
    "http://x.coolkit.cc/a'); DROP TABLE offer_links;--", "http://x.coolkit.cc/a b",
    " http://x.coolkit.cc/a", "http://x.coolkit.cc/a ", "http://x.coolkit.cc/a\n",
    // not text
    "", null, 5, ["http://x.coolkit.cc/a"], { url: "http://x.coolkit.cc/a" },
  ];
  for (const bad of refused) {
    assert.equal(linkParts(bad), null, JSON.stringify(bad));
  }
});

test("an address is public only outside every range no public server is in", () => {
  for (const address of ["1.2.3.4", "8.8.8.8", "100.63.255.255", "100.128.0.0", "172.15.255.255", "172.32.0.0",
    "192.0.1.255", "192.0.3.0", "198.17.255.255", "198.20.0.0", "223.255.255.255", "11.0.0.0"]) {
    assert.equal(isPublicAddress(address), true, address);
  }
  for (const address of ["0.0.0.0", "0.255.255.255", "10.0.0.1", "10.255.255.255", "100.64.0.0", "100.127.255.255",
    "127.0.0.1", "169.254.1.1", "172.16.0.1", "172.31.255.255", "192.0.0.8", "192.0.2.50", "192.88.99.1",
    "192.168.99.50", "198.18.0.1", "198.19.255.255", "198.51.100.7", "203.0.113.9", "224.0.0.1", "239.255.255.255",
    "240.0.0.1", "255.255.255.255", "x.coolkit.cc", "1.2.3.04"]) {
    assert.equal(isPublicAddress(address), false, address);
  }
});

test("an offer's url is kept as its parts, pending, peeked at once and never fetched unapproved", async () => {
  const bindings = env();
  const fetcher = server();
  assert.deepEqual(await results(bindings, [URL_OFFER], { fetcher }), ["taken"]);
  assert.deepEqual(linkRows(bindings),
    [{ sha: sha(APP), path: URL_PATH, scheme: "http", host: ADDRESS, port: 8088, review: "pending" }]);
  const [{ object }] = rows(bindings, "SELECT object FROM readouts");
  assert.deepEqual(JSON.parse(bindings.READOUTS.objects.get(object).body).offered,
    [{ name: URL_FILE.name, version: "1.2.0", sha256: sha(APP), url: BY_ADDRESS }], "its manifest names the url");
  // Only the peek, a Range at each of eWeLink's servers by name: the address never.
  assert.deepEqual(fetcher.asked.map((each) => [each.url, each.range, each.redirect]),
    CLOUDS.ewelink.servers.map((base) => [`${base}${URL_PATH}?deviceid=0&ts=1&sign=0`, `bytes=0-${PEEK_BYTES - 1}`,
      "manual"]));
  assert.ok(fetcher.asked.every((each) => !each.url.includes(ADDRESS)));
  assert.deepEqual(rows(bindings, "SELECT outcome, status, server FROM link_peeks"),
    [{ outcome: "answered", status: 404, server: "cn-otadl.coolkit.cn:8088" }], "the last server's answer kept");
  // Sent again by others, and agreed: no second peek, and nothing fetched, first sight or nightly.
  await agree(bindings, URL_OFFER, fetcher, SENDERS.slice(1));
  await retryOffers(bindings, TOMORROW, { totalBytes: api.TOTAL_BYTES, fetcher });
  assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1);
  assert.equal(fetcher.asked.length, CLOUDS.ewelink.servers.length, "the one peek alone");
  assert.equal(peeks(bindings).length, 1);
  assert.ok(!bindings.READOUTS.objects.has(`readouts/firmware/${sha(APP)}`));
});

test("an offer whose url breaks a rule, or names a path as well, is refused, the rest taken", async () => {
  const bad = ["http://192.168.99.50:8088/a", "http://x.coolkit.cc/a?b=1", "http://x.coolkit.cc/a'b",
    "http://x.coolkit.cc/a b", "http://x.coolkit.cc/a\nb", "http://x.coolkit.cc/$(id)", "http://x.coolkit.cc/a;b",
    "http://x.coolkit.cc/a|b", "http://x.coolkit.cc/`id`", "http://x.coolkit.cc/<a>", "http://x.coolkit.cc/%3Cx%3E",
    "http://evil.example/a", 7, null];
  const offers = [...bad.map((url, at) => ({ ...OFFER, stock_version: `1.0.${at}`, files: [{ ...URL_FILE, url }] })),
    { ...OFFER, stock_version: "2.0.0", files: [{ ...URL_FILE, path: PATH }] }, URL_OFFER];
  const bindings = env();
  const fetcher = server();
  const answered = (await send(bindings, offers, { fetcher })).offers;
  assert.deepEqual(answered.map((each) => each.result), [...Array(bad.length + 1).fill("refused"), "taken"]);
  assert.match(answered[0].why, /url/);
  assert.equal(linkRows(bindings).length, 1);
});

test("a whole readout's url beside its file is kept pending, the file held; one that breaks a rule turns it away",
  async () => {
    const bindings = env();
    const fetcher = server();
    assert.equal(await readout(bindings, "192.0.2.1", 64501,
      { read: true, linked: { entry: { link: undefined, url: BY_NAME } } }, fetcher), 201);
    assert.deepEqual(linkRows(bindings),
      [{ sha: sha(APP), path: URL_PATH, scheme: "https", host: "global-otadl2bsy.coolkit.cc", port: null,
        review: "pending" }]);
    assert.equal(rows(bindings, "SELECT * FROM readout_firmware").length, 1, "its file held, as carried");
    assert.deepEqual(fetcher.asked.map((each) => [each.url, each.range]), [[BY_NAME_ASKED, `bytes=0-${PEEK_BYTES - 1}`]],
      "a name of eWeLink's peeked at as sent, with the query");
    for (const entry of [{ link: undefined, url: "http://10.0.0.1/a" }, { link: undefined, url: `${BY_NAME}?x=1` },
      { url: BY_NAME }, { link: undefined, url: "http://x.coolkit.cc/a'||'b" }]) {
      const other = env();
      const none = server();
      assert.equal(await readout(other, "192.0.2.1", 64501, { read: true, linked: { entry } }, none), 400,
        JSON.stringify(entry));
      assert.deepEqual(traces(other), NOTHING);
      assert.deepEqual(none.asked, []);
    }
  });

// A link pending review of `url` for APP, by a first stage from one sender: its readout.
async function pending(bindings, url = BY_ADDRESS) {
  await results(bindings, [{ ...OFFER, files: [{ ...URL_FILE, url }] }], { fetcher: server() });
  return rows(bindings, "SELECT readout FROM offer_links")[0].readout;
}

test("approving a link by address fetches its path from eWeLink's servers by name, never the address", async () => {
  const bindings = env();
  const id = await pending(bindings);
  const other = Buffer.from("not the file");
  // The US server gives another file, Asia's the file.
  const fetcher = server((url) => (url === SERVER_URL("us-otadl.coolkit.cc") ? new Response(other)
    : url === SERVER_URL("as-otadl.coolkit.cc") ? new Response(APP) : new Response("", { status: 404 })));
  assert.equal(await approveLink(bindings, id, sha(APP), NOW, { totalBytes: api.TOTAL_BYTES, fetcher }), "kept");
  assert.deepEqual(fetcher.asked.map((each) => each.url),
    ["eu-otadl.coolkit.cc", "us-otadl.coolkit.cc", "as-otadl.coolkit.cc"].map((name) => SERVER_URL(name)));
  assert.ok(fetcher.asked.every((each) => each.redirect === "manual" && each.range === null));
  assert.equal(linkRows(bindings)[0].review, "approved");
  assert.equal(rows(bindings, "SELECT readout, sha FROM readout_firmware")[0].readout, id);
  assert.equal(Buffer.from(bindings.READOUTS.objects.get(`readouts/firmware/${sha(APP)}`).body).toString(), APP.toString());
  assert.equal(await approveLink(bindings, id, sha(APP), NOW, { totalBytes: api.TOTAL_BYTES, fetcher }), "held");
  assert.equal(fetcher.asked.length, 3, "held: not fetched again");
});

test("approving a link by eWeLink's name fetches it as given, with the query; one giving no file is kept approved, said so",
  async () => {
    const bindings = env();
    const id = await pending(bindings, BY_NAME);
    const fetcher = server((url) => (url === BY_NAME_ASKED ? new Response("another file") : new Response("", { status: 404 })));
    assert.equal(await approveLink(bindings, id, sha(APP), NOW, { totalBytes: api.TOTAL_BYTES, fetcher }), "failed");
    assert.deepEqual(fetcher.asked.map((each) => each.url), [BY_NAME_ASKED]);
    assert.equal(linkRows(bindings)[0].review, "approved");
    assert.deepEqual(rows(bindings, "SELECT path, why FROM offer_fetch_failures"), [{ path: BY_NAME, why: "other" }]);
    assert.equal(rows(bindings, "SELECT * FROM readout_firmware").length, 0, "nothing kept");
    // Tried again, it gives the file.
    const now = server((url) => (url === BY_NAME_ASKED ? new Response(APP) : new Response("", { status: 404 })));
    assert.equal(await approveLink(bindings, id, sha(APP), NOW, { totalBytes: api.TOTAL_BYTES, fetcher: now }), "kept");
  });

test("approving is within OFFER_FETCHES_DAILY; a link rejected is never fetched, nor approved after", async () => {
  const bindings = env();
  const id = await pending(bindings);
  bindings.DB.db.prepare("INSERT OR REPLACE INTO counters (name, day, count) VALUES (?, ?, ?)")
    .run(`offer-fetches:${DAY}`, DAY, OFFER_FETCHES_DAILY);
  const fetcher = server(() => new Response(APP));
  assert.equal(await approveLink(bindings, id, sha(APP), NOW, { totalBytes: api.TOTAL_BYTES, fetcher }), "capped");
  assert.deepEqual(fetcher.asked, []);
  const other = env();
  const theirs = await pending(other);
  assert.equal(await rejectLink(other.DB, theirs, sha(APP)), true);
  assert.equal(linkRows(other)[0].review, "rejected");
  assert.equal(await approveLink(other, theirs, sha(APP), NOW, { totalBytes: api.TOTAL_BYTES, fetcher }), "none");
  await agree(other, URL_OFFER, fetcher, SENDERS.slice(1));
  await retryOffers(other, TOMORROW, { totalBytes: api.TOTAL_BYTES, fetcher });
  assert.deepEqual(fetcher.asked.filter((each) => each.range === null), [], "only peeks, never a fetch");
  assert.deepEqual(linkRows(other).map((row) => row.review), ["rejected", "rejected", "rejected"],
    "sent again, it is taken as rejected");
  assert.equal(await rejectLink(other.DB, theirs, sha(APP)), false, "rejected once");
});

test("one approval approves the link wherever it was sent, and another link of the file stays pending", async () => {
  const bindings = env();
  await agree(bindings, URL_OFFER, server(), SENDERS);
  await results(bindings, [{ ...OFFER, stock_version: "9.0.0", files: [{ ...URL_FILE, url: BY_NAME }] }],
    { fetcher: server() });
  const [{ readout: id }] = rows(bindings, "SELECT readout FROM offer_links WHERE host = ?", ADDRESS);
  assert.equal(await approveLink(bindings, id, sha(APP), NOW, { totalBytes: api.TOTAL_BYTES, fetcher: server() }),
    "failed");
  assert.deepEqual(rows(bindings, "SELECT host, review, count(*) AS n FROM offer_links GROUP BY host, review ORDER BY host"),
    [{ host: ADDRESS, review: "approved", n: 3 }, { host: "global-otadl2bsy.coolkit.cc", review: "pending", n: 1 }]);
});

test("a peek reads PEEK_BYTES at most, the Range ignored, and says what the bytes start like", async () => {
  assert.equal(PEEK_BYTES, 100);
  const long = Buffer.concat([wrapped(["FWSW-BK-S61-BK7238"]), Buffer.alloc(OFFER_FETCH_MAX_BYTES, 7)]);
  let pulled = 0;
  const body = () => new ReadableStream({
    pull(controller) {
      const part = long.subarray(pulled, pulled + 64);
      pulled += part.length;
      if (part.length) controller.enqueue(new Uint8Array(part)); else controller.close();
    },
  });
  const bindings = env();
  await pending(bindings, BY_NAME);
  assert.equal(peeks(bindings)[0].status, 404);
  const fresh = env();
  const fetcher = server(() => new Response(body(), { status: 200 }));
  await results(fresh, [{ ...URL_OFFER, files: [{ ...URL_FILE, url: BY_NAME }] }], { fetcher });
  const [peek] = peeks(fresh);
  assert.equal(peek.hex, long.subarray(0, PEEK_BYTES).toString("hex"));
  assert.ok(pulled < 4096, "the rest never read");
  assert.deepEqual([peek.outcome, peek.status, peek.size, peek.server, peek.kind, peek.names],
    ["answered", 200, null, "global-otadl2bsy.coolkit.cc", "sonoff-wrapper", "FWSW-BK-S61-BK7238"]);
  // Each kind; the size from Content-Range.
  const ranged = env();
  await results(ranged, [{ ...URL_OFFER, files: [{ ...URL_FILE, url: BY_NAME }] }], { fetcher: server(() =>
    new Response(Buffer.from([0x1e, 0xf1, 0xee, 0x1b, 1, 2]), { status: 206,
      headers: { "Content-Range": "bytes 0-99/123456" } })) });
  assert.deepEqual(rows(ranged, "SELECT status, size, kind, hex FROM link_peeks"),
    [{ status: 206, size: 123456, kind: "beken-fota", hex: "1ef1ee1b0102" }]);
  for (const [start, kind] of [[[0x03, 0x01], "sonoff-wrapper"], [[0xea, 0x04], "esp8266"], [[0x50, 0x4b], "zip"],
    [[0x1e, 0xf1, 0xee, 0x1b], "beken-fota"], [[0x1e, 0xf1], "unknown"], [[0x3c, 0x68], "unknown"], [[], "unknown"]]) {
    assert.equal(kindOf(Uint8Array.from(start)), kind, kind);
  }
});

test("a peek never follows a redirect, and a timeout or no answer is kept as such", async () => {
  for (const [answer, seen] of [
    [() => new Response(null, { status: 302, headers: { Location: "http://10.0.0.1/x<y>" } }),
      { outcome: "redirect", status: 302, location: "http://10.0.0.1/x<y>", hex: null }],
    [() => { throw new DOMException("too slow", "TimeoutError"); }, { outcome: "timeout", status: null, location: null, hex: null }],
    [() => { throw new TypeError("connection refused"); }, { outcome: "failed", status: null, location: null, hex: null }],
    [() => new Response("", { status: 404 }), { outcome: "answered", status: 404, location: null, hex: "" }],
  ]) {
    const bindings = env();
    const fetcher = server(answer);
    await results(bindings, [{ ...URL_OFFER, files: [{ ...URL_FILE, url: BY_NAME }] }], { fetcher });
    assert.deepEqual(rows(bindings, "SELECT outcome, status, location, hex FROM link_peeks"), [seen], seen.outcome);
    assert.deepEqual(fetcher.asked.map((each) => [each.url, each.redirect]), [[BY_NAME_ASKED, "manual"]], "once, not followed");
  }
});

test("peeks count against the day's fetches and the sender's first sights; one with no room is made when next sent",
  async () => {
    const bindings = env();
    const fetcher = server();
    // Five other offers' links use the sender's first sights.
    const five = [0, 1, 2, 3, 4].map((n) => ({ ...OFFER, stock_version: `5.0.${n}`,
      files: [{ ...FILE, sha256: sha(`f${n}`), path: `/ota/rom/${TOKEN}/f${n}.bin` }] }));
    await results(bindings, [...five, { ...URL_OFFER, files: [{ ...URL_FILE, url: BY_NAME }] }],
      { address: "192.0.2.1", asn: 64501, fetcher });
    assert.equal(fetcher.asked.length, OFFER_FIRST_SIGHT_PER_SENDER);
    assert.deepEqual(peeks(bindings), [], "no room: not peeked, nor marked as peeked");
    await results(bindings, [{ ...URL_OFFER, files: [{ ...URL_FILE, url: BY_NAME }] }],
      { address: "198.51.100.2", asn: 64502, fetcher });
    assert.equal(peeks(bindings).length, 1);
    assert.equal((await read(bindings.DB, firstSightsOn(DAY))).count, OFFER_FIRST_SIGHT_PER_SENDER + 1);
    assert.equal((await read(bindings.DB, `offer-fetches:${DAY}`)).count, 0, "none of agreement's");
    // With the day's first sights used, none.
    const full = env();
    await results(full, [], {});
    full.DB.db.prepare("INSERT INTO counters (name, day, count) VALUES (?, ?, ?)").run(firstSightsOn(DAY), DAY,
      FIRST_SIGHT_DAILY);
    const none = server();
    await results(full, [URL_OFFER], { fetcher: none });
    assert.deepEqual([none.asked, peeks(full)], [[], []]);
  });

// What is kept of links to review, their peeks and failures, held to bounds.
// Link `n` to review, of a file of its own, by a public address made up here.
const reviewUrl = (n) => `http://1.2.3.4:8088/ota/s61/r${n}.rbl`;
const reviewOffer = (n) => ({ ...OFFER, stock_version: `7.0.${n}`,
  files: [{ ...URL_FILE, sha256: sha(`review ${n}`), url: reviewUrl(n) }] });
// The links to review waiting, each once however many sent it.
const waiting = (bindings) => rows(bindings, `SELECT DISTINCT sha, host, path FROM offer_links
  WHERE review = 'pending'`).length;

test("past PENDING_LINKS_MOST links waiting, a new one is neither kept nor peeked, its offer taken, and counted",
  async () => {
    assert.equal(PENDING_LINKS_MOST, 100);
    const bindings = env();
    const fill = bindings.DB.db.prepare(`INSERT INTO offer_links (readout, sha, path, cloud, scheme, host, port, review)
      VALUES ('2026-11-01-000000000000', ?, ?, 'ewelink', 'http', '5.6.7.8', NULL, 'pending')`);
    for (let n = 0; n < PENDING_LINKS_MOST - 1; n += 1) {
      fill.run(sha(`filler ${n}`), `/f${n}.bin`);
    }
    const fetcher = server();
    assert.deepEqual(await results(bindings, [reviewOffer(1)], { fetcher }), ["taken"], "the last place");
    assert.equal(waiting(bindings), PENDING_LINKS_MOST);
    const asked = fetcher.asked.length;
    assert.deepEqual(await results(bindings, [reviewOffer(2)], { address: "198.51.100.2", fetcher }), ["taken"]);
    assert.equal(waiting(bindings), PENDING_LINKS_MOST, "not kept");
    assert.equal(rows(bindings, "SELECT * FROM offer_links WHERE path = '/ota/s61/r2.rbl'").length, 0);
    assert.equal(fetcher.asked.length, asked, "nor peeked");
    assert.equal(rows(bindings, "SELECT * FROM readouts WHERE offered = '1.2.0'").length, 2, "its offer taken");
    assert.equal((await read(bindings.DB, linksNotKeptOn(DAY))).count, 1);
    // One waiting already, sent again, is kept: another network for it.
    assert.deepEqual(await results(bindings, [reviewOffer(1)], { address: "203.0.113.3", asn: 64503, fetcher }),
      ["taken"]);
    assert.equal(rows(bindings, "SELECT * FROM offer_links WHERE path = '/ota/s61/r1.rbl'").length, 2);
    assert.equal((await read(bindings.DB, linksNotKeptOn(DAY))).count, 1);
  });

test("one sender puts PENDING_LINKS_PER_SENDER links to review in the queue a day, counted by its /24", async () => {
  assert.equal(PENDING_LINKS_PER_SENDER, 5);
  const bindings = env();
  const six = [0, 1, 2, 3, 4, 5].map(reviewOffer);
  assert.deepEqual(await results(bindings, six.slice(0, 4), { address: "198.51.100.7", fetcher: server() }),
    Array(4).fill("taken"));
  assert.deepEqual(await results(bindings, six.slice(4), { address: "198.51.100.8", fetcher: server() }),
    ["taken", "taken"], "another address of the /24, its offers taken all the same");
  assert.equal(waiting(bindings), PENDING_LINKS_PER_SENDER);
  assert.equal((await read(bindings.DB, linksNotKeptOn(DAY))).count, 1);
  assert.deepEqual(await results(bindings, [reviewOffer(6)], { address: "198.51.100.8", fetcher: server(),
    now: TOMORROW }), ["taken"], "the next day");
  assert.equal(waiting(bindings), PENDING_LINKS_PER_SENDER + 1);
});

test("links sent only from networks that count as none hold RENTED_SHARE of the queue at most, owners' the rest",
  async () => {
    const share = (PENDING_LINKS_MOST * api.RENTED_SHARE) / 100;
    assert.equal(PENDING_LINKS_RENTED, share);
    const bindings = env();
    // Waiting, sent by a readout from no network that counts (no readout_networks row).
    const fill = bindings.DB.db.prepare(`INSERT INTO offer_links (readout, sha, path, cloud, scheme, host, port, review)
      VALUES ('2026-11-01-000000000000', ?, ?, 'ewelink', 'http', '5.6.7.8', NULL, 'pending')`);
    for (let n = 0; n < share - 1; n += 1) {
      fill.run(sha(`rented ${n}`), `/r${n}.bin`);
    }
    const fetcher = server();
    await results(bindings, [reviewOffer(1)], { address: "198.51.100.2", asn: 16509, fetcher });
    assert.equal(waiting(bindings), share, "the share's last place");
    await results(bindings, [reviewOffer(2)], { address: "203.0.113.2", asn: 16509, fetcher });
    assert.equal(waiting(bindings), share, "past the share: not kept");
    assert.equal((await read(bindings.DB, linksNotKeptOn(DAY))).count, 1);
    await results(bindings, [reviewOffer(3)], { address: "192.0.2.3", asn: 64503, fetcher });
    assert.equal(waiting(bindings), share + 1, "an owner's network still has room");
    // A rented link an owner's network sends too is the owners' now, freeing a rented place.
    await results(bindings, [reviewOffer(1)], { address: "192.0.2.4", asn: 64504, fetcher });
    await results(bindings, [reviewOffer(2)], { address: "203.0.113.9", asn: 16509, fetcher });
    assert.equal(waiting(bindings), share + 2, "another rented sender's, now within the share");
  });

test("a link to review waiting already takes none of its sender's day, and one the full queue refuses is given back",
  async () => {
    const bindings = env();
    const fetcher = server();
    // One new link, in six offers: one of the day's places.
    const same = [0, 1, 2, 3, 4, 5].map((n) => ({ ...reviewOffer(0), stock_version: `8.0.${n}` }));
    assert.deepEqual(await results(bindings, same, { address: "198.51.100.7", fetcher }), Array(6).fill("taken"));
    assert.equal(rows(bindings, "SELECT * FROM offer_links WHERE review = 'pending'").length, 6, "each kept");
    assert.equal((await read(bindings.DB, linksNotKeptOn(DAY))).count, 0);
    // The queue full, a new one is refused and its place given back.
    const fill = bindings.DB.db.prepare(`INSERT INTO offer_links (readout, sha, path, cloud, scheme, host, port, review)
      VALUES ('2026-11-01-000000000000', ?, ?, 'ewelink', 'http', '5.6.7.8', NULL, 'pending')`);
    for (let n = 0; n < PENDING_LINKS_MOST - 1; n += 1) {
      fill.run(sha(`filler ${n}`), `/f${n}.bin`);
    }
    await results(bindings, [reviewOffer(1)], { address: "198.51.100.7", fetcher });
    assert.equal((await read(bindings.DB, linksNotKeptOn(DAY))).count, 1);
    bindings.DB.db.prepare("DELETE FROM offer_links WHERE readout = '2026-11-01-000000000000'").run();
    // Room again: the sender's day has every place but the first link's.
    const five = [2, 3, 4, 5, 6].map(reviewOffer);
    await results(bindings, five, { address: "198.51.100.7", fetcher });
    assert.equal(waiting(bindings), PENDING_LINKS_PER_SENDER);
    assert.equal((await read(bindings.DB, linksNotKeptOn(DAY))).count, 2, "the sixth new link, past the day's");
  });

// Each of linkPruning's statements run as the nightly job runs them: how many each deleted.
async function prune(bindings, today) {
  const named = Object.entries(linkPruning(bindings.DB, today));
  const done = await bindings.DB.batch(named.map(([, statement]) => statement));
  return Object.fromEntries(named.map(([name], at) => [name, done[at].meta.changes]));
}

test("a rejected link stays rejected after the readouts that sent it are gone, the newest REJECTED_LINKS_MOST kept",
  async () => {
    assert.equal(REJECTED_LINKS_MOST, 10_000);
    const bindings = env();
    const id = await pending(bindings);
    assert.equal(await rejectLink(bindings.DB, id, sha(APP), NOW), true);
    assert.deepEqual(rows(bindings, "SELECT sha, cloud, link, day FROM rejected_links"),
      [{ sha: sha(APP), cloud: "ewelink", link: BY_ADDRESS, day: DAY }]);
    // Its readout gone, as pending.js deletes one, and the link sent again.
    for (const table of ["offer_links", "readout_networks", "readouts"]) {
      bindings.DB.db.prepare(`DELETE FROM ${table}`).run();
    }
    const fetcher = server();
    assert.deepEqual(await results(bindings, [URL_OFFER], { address: "198.51.100.2", fetcher, now: TOMORROW }),
      ["taken"]);
    assert.deepEqual(linkRows(bindings).map((row) => row.review), ["rejected"], "never back for review");
    assert.deepEqual(fetcher.asked, [], "nor peeked");
    // Bounded: past the newest REJECTED_LINKS_MOST, the oldest goes.
    const newer = bindings.DB.db.prepare("INSERT INTO rejected_links (sha, cloud, link, day) VALUES (?, 'ewelink', ?, ?)");
    for (let n = 0; n < REJECTED_LINKS_MOST; n += 1) {
      newer.run(sha(`newer ${n}`), reviewUrl(n), "2026-11-08");
    }
    assert.equal((await prune(bindings, "2026-11-09")).rejected, 1);
    assert.equal(rows(bindings, "SELECT * FROM rejected_links WHERE sha = ?", sha(APP)).length, 0, "the oldest");
    assert.equal(rows(bindings, "SELECT count(*) AS n FROM rejected_links")[0].n, REJECTED_LINKS_MOST);
  });

test("a link pending PENDING_LINK_DAYS is deleted with its peek; peeks and failures of links gone, and old failures, too",
  async () => {
    assert.equal(PENDING_LINK_DAYS, 180);
    const bindings = env();
    // A link to review sent by an offer that was then taken as right: kept for good, its link not.
    await agree(bindings, URL_OFFER, server(), SENDERS, new Date("2026-05-01T12:00:00Z"));
    await results(bindings, [{ ...OFFER, stock_version: "8.0.0", files: [{ ...URL_FILE, url: BY_NAME }] }],
      { fetcher: server() });
    assert.equal(peeks(bindings).length, 2);
    const failed = bindings.DB.db.prepare(`INSERT INTO offer_fetch_failures (sha, cloud, path, why, day, since)
      VALUES (?, 'ewelink', ?, ?, ?, ?)`);
    failed.run(sha(APP), BY_NAME, "none", "2026-11-06", "2026-11-01");   // its link still sent
    failed.run(sha(APP), "/ota/rom/gone/user1.bin", "other", "2026-11-06", "2026-11-06");   // no link gives it
    failed.run(sha("old"), PATH, "none", "2026-05-01", "2026-05-01");   // last tried long ago
    bindings.DB.db.prepare("INSERT INTO offer_links (readout, sha, path, cloud) VALUES (?, ?, ?, 'ewelink')")
      .run(rows(bindings, "SELECT id FROM readouts WHERE key = ? LIMIT 1", KEY)[0].id, sha("old"), PATH);
    assert.deepEqual(await prune(bindings, DAY), { links: 3, peeks: 1, failures: 2, rejected: 0 });
    assert.deepEqual(rows(bindings, "SELECT DISTINCT host, review FROM offer_links WHERE host IS NOT NULL"),
      [{ host: "global-otadl2bsy.coolkit.cc", review: "pending" }], "the one sent this month kept");
    assert.deepEqual(peeks(bindings).map((peek) => peek.link), [BY_NAME]);
    assert.deepEqual(rows(bindings, "SELECT path FROM offer_fetch_failures"), [{ path: BY_NAME }]);
    assert.equal(rows(bindings, "SELECT * FROM readouts").length, 4, "the readouts themselves stay");
  });

test("first sights have a day of their own, rented networks a share of it, and agreement its own", async () => {
  assert.equal(FIRST_SIGHT_RENTED_DAILY, Math.floor((FIRST_SIGHT_DAILY * api.RENTED_SHARE) / 100));
  const bindings = env();
  const junk = cloud({});
  // Made-up links from many addresses of a host's network (Amazon's, which counts as none).
  for (let n = 0; n < 10; n += 1) {
    const offers = [0, 1, 2, 3, 4].map((m) => ({ ...OFFER, stock_model: `X${n}-${m}`,
      files: [{ ...FILE, sha256: sha(`junk ${n} ${m}`), path: `/ota/rom/${TOKEN}/j${n}${m}.bin` }] }));
    await results(bindings, offers, { address: `100.${n}.0.1`, asn: 16509, fetcher: junk });
  }
  assert.equal(junk.asked.length, FIRST_SIGHT_RENTED_DAILY, "rented networks' share, and no more");
  assert.equal((await read(bindings.DB, rentedFirstSightsOn(DAY))).count, FIRST_SIGHT_RENTED_DAILY);
  // Owners' first sights use the rest; agreement still fetches once those are gone.
  bindings.DB.db.prepare("UPDATE counters SET count = ? WHERE name = ?").run(FIRST_SIGHT_DAILY, firstSightsOn(DAY));
  const real = cloud();
  await agree(bindings, OFFER, real);
  assert.deepEqual(real.asked, [URL_OF(PATH)], "fetched by agreement");
  assert.ok(bindings.READOUTS.objects.has(`readouts/firmware/${sha(APP)}`));
});

test("a sender's addresses in one IPv4 /24 share its first sights", async () => {
  const bindings = env();
  const fetcher = cloud({});
  for (const [at, address] of ["198.51.100.20", "198.51.100.21"].entries()) {
    const offers = [0, 1, 2, 3, 4].map((m) => ({ ...OFFER, stock_model: `Y${at}-${m}`,
      files: [{ ...FILE, sha256: sha(`y ${at} ${m}`), path: `/ota/rom/${TOKEN}/y${at}${m}.bin` }] }));
    await results(bindings, offers, { address, asn: 64501, fetcher });
  }
  assert.equal(fetcher.asked.length, OFFER_FIRST_SIGHT_PER_SENDER);
});

// Every row's bytes in the counters the limits are held to: the store's, and
// those of readouts waiting for review.
async function countersMatch(bindings) {
  const [{ all, waiting: left }] = rows(bindings, `SELECT coalesce(sum(bytes), 0) AS "all",
    coalesce(sum(CASE WHEN review = 'pending' THEN bytes END), 0) AS waiting FROM readouts`);
  assert.equal((await read(bindings.DB, READOUTS_EVER)).bytes, all, "the store's count");
  assert.equal((await read(bindings.DB, PENDING)).bytes, left, "the pending count");
}

test("a file fetched for a readout waiting for review counts towards those waiting, until it is taken as right",
  async () => {
    const bindings = env();
    await results(bindings, [{ ...OFFER, stock_model: "OTHER", files: [{ ...FILE, sha256: sha("other"),
      path: `/ota/rom/${TOKEN}/other.bin` }] }], { address: "192.0.2.9", fetcher: cloud({}) });
    await results(bindings, [OFFER], { address: "192.0.2.1", asn: 64501 });
    assert.ok(bindings.READOUTS.objects.has(`readouts/firmware/${sha(APP)}`), "fetched on first sight");
    await countersMatch(bindings);
    await agree(bindings, OFFER, cloud(), SENDERS.slice(1));
    assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1);
    await countersMatch(bindings);
  });

test("a made-up readout's file that an offer taken as right links is kept when the readout is deleted", async () => {
  const bindings = env();
  // A stranger names the real file under a made-up model: first sight fetches it under theirs.
  await results(bindings, [{ ...OFFER, model: "S61", stock_model: "MADEUP" }], { address: "192.0.2.50", asn: 64999 });
  const [{ id: theirs }] = rows(bindings, "SELECT id FROM readouts");
  assert.equal(rows(bindings, "SELECT readout FROM readout_firmware")[0].readout, theirs);
  const fetcher = cloud();
  await agree(bindings, OFFER, fetcher);
  assert.deepEqual(fetcher.asked, [], "held: not fetched again");
  const pruned = await prunePending(bindings, "2027-06-01");
  assert.equal(pruned.readouts, 1);
  assert.ok(bindings.READOUTS.objects.has(`readouts/firmware/${sha(APP)}`), "the file stays");
  const [mark] = rows(bindings, `SELECT readouts.key, readouts.review FROM readout_firmware
    JOIN readouts ON readouts.id = readout_firmware.readout`);
  assert.deepEqual(mark, { key: KEY, review: "quorum" }, "listed under a readout of the offer agreed");
  assert.equal(rows(bindings, "SELECT count(*) AS n FROM offer_links WHERE bytes IS NOT NULL")[0].n, 1);
  await countersMatch(bindings);
});

test("a file's mark moves alone, beside others' marks of it, and a failed night deletes nothing from the store",
  async () => {
    const bindings = env();
    await results(bindings, [{ ...OFFER, model: "S61", stock_model: "MADEUP" }], { address: "192.0.2.50", asn: 64999 });
    const [{ id: theirs }] = rows(bindings, "SELECT id FROM readouts");
    await agree(bindings, OFFER, cloud());
    // Every readout agreed marks the file too, as whole readouts sent together may, and one more.
    const agreed = rows(bindings, "SELECT id FROM readouts WHERE review = 'quorum'").map((row) => row.id);
    for (const id of [...agreed, "another"]) {
      await bindings.DB.prepare("INSERT OR IGNORE INTO readout_firmware (readout, sha) VALUES (?1, ?2)").bind(id, sha(APP)).run();
    }
    const objects = [...bindings.READOUTS.objects.keys()];
    const batch = bindings.DB.batch;
    bindings.DB.batch = async () => { throw new Error("D1 is down"); };
    await assert.rejects(prunePending(bindings, "2027-06-01"));
    assert.deepEqual([...bindings.READOUTS.objects.keys()], objects, "nothing deleted that its rows still name");
    bindings.DB.batch = batch;
    assert.equal((await prunePending(bindings, "2027-06-01")).readouts, 1);
    assert.ok(bindings.READOUTS.objects.has(`readouts/firmware/${sha(APP)}`), "the file stays");
    const marks = rows(bindings, "SELECT readout FROM readout_firmware ORDER BY readout").map((row) => row.readout);
    assert.deepEqual(marks, [...agreed, "another"].sort(), "each other readout keeps its own mark");
    assert.ok(!marks.includes(theirs));
  });

test("each night tries the offers tried longest ago first, and a link no more once it has given nothing a month",
  async () => {
    assert.equal(OFFER_RETRY_DAYS, 30);
    const bindings = env();
    const none = cloud({});
    // More offers whose files never come than a night tries, then one whose file will.
    const dead = (n) => ({ ...OFFER, stock_model: `DEAD${n}`, files: [{ ...FILE, sha256: sha(`gone ${n}`),
      path: `/ota/rom/${TOKEN}/g${n}.bin` }] });
    for (let n = 0; n < OFFER_RETRY_EACH_NIGHT; n += 1) {
      await agree(bindings, dead(n), none);
    }
    const real = { ...OFFER, stock_model: "ZREAL" };
    await agree(bindings, real, none);
    const night = (days) => new Date(NOW.getTime() + days * 86_400_000);
    const fetcher = cloud();
    await retryOffers(bindings, night(1), { totalBytes: api.TOTAL_BYTES, fetcher });
    await retryOffers(bindings, night(2), { totalBytes: api.TOTAL_BYTES, fetcher });
    assert.equal(fetcher.asked.filter((url) => url === URL_OF(PATH)).length, 1, "the second night at the latest");
    assert.ok(bindings.READOUTS.objects.has(`readouts/firmware/${sha(APP)}`));
    // A month on, the dead links are tried no more.
    const late = cloud({});
    assert.equal((await retryOffers(bindings, night(OFFER_RETRY_DAYS + 1), { totalBytes: api.TOTAL_BYTES,
      fetcher: late })).offers, 0);
    assert.deepEqual(late.asked, []);
  });

test("a link that gave another file is not retried, and takes no night's place", async () => {
  const bindings = env();
  const wrong = cloud({ [URL_OF(PATH)]: Buffer.from("another file") });
  await agree(bindings, OFFER, wrong);
  assert.deepEqual(rows(bindings, "SELECT why FROM offer_fetch_failures"), [{ why: "other" }]);
  assert.equal((await retryOffers(bindings, TOMORROW, { totalBytes: api.TOTAL_BYTES, fetcher: wrong })).offers, 0);
});

test("a link that no longer keeps the rules is noted as failed, and takes no night's place", async () => {
  const bindings = env();
  await agree(bindings, OFFER, cloud({}));
  // As if the rules changed since it was kept: its path no longer of eWeLink's shape.
  bindings.DB.db.prepare("UPDATE offer_links SET path = '/not/a/link.bin'").run();
  bindings.DB.db.prepare("DELETE FROM offer_fetch_failures").run();
  const fetcher = cloud();
  assert.equal((await retryOffers(bindings, TOMORROW, { totalBytes: api.TOTAL_BYTES, fetcher })).offers, 1);
  assert.deepEqual(fetcher.asked, [], "never fetched");
  assert.deepEqual(rows(bindings, "SELECT why FROM offer_fetch_failures"), [{ why: "other" }]);
  const night = new Date(TOMORROW.getTime() + 86_400_000);
  assert.equal((await retryOffers(bindings, night, { totalBytes: api.TOTAL_BYTES, fetcher })).offers, 0);
});

test("fetches after the answer end by AFTER_ANSWER_MS: one with too little time left is not begun", async () => {
  assert.equal(AFTER_ANSWER_MS, 25_000);
  const bindings = env();
  const OTHER = Buffer.from("another file eWeLink offers");
  const path = `/ota/rom/${TOKEN}/user2.1024.new.2.bin`;
  const two = { ...OFFER, files: [FILE, { ...FILE, name: "user2.bin", sha256: sha(OTHER), path }] };
  const files = cloud({ [URL_OF(PATH)]: APP, [URL_OF(path)]: OTHER });
  const realNow = Date.now;
  let clock = realNow();
  // Each fetch takes 24 seconds of the clock.
  const slow = async (url, init) => {
    clock += 24_000;
    return files(url);
  };
  const waits = [];
  Date.now = () => clock;
  try {
    const response = await api.offers(request({ offers: [two] }), bindings, NOW, { waitUntil: (p) => waits.push(p) },
      slow);
    assert.equal(response.status, 200);
    await Promise.all(waits);
  } finally {
    Date.now = realNow;
  }
  assert.equal(files.asked.length, 1, "the second not begun with a second left");
  assert.equal(rows(bindings, "SELECT * FROM readout_firmware").length, 1);
  assert.deepEqual(rows(bindings, "SELECT * FROM offer_fetch_failures"), [], "and not noted as failed");
});

test("a peek cut off before it was kept is claimed again a later day, not the same one", async () => {
  const bindings = env();
  const id = await pending(bindings);
  // As a Worker stopped after its claim leaves it.
  const cut = (day) => bindings.DB.db.prepare(`UPDATE link_peeks SET server = NULL, outcome = 'failed', status = NULL,
    day = ?`).run(day);
  cut(DAY);
  const today = server();
  await peekNamed(bindings, id, NOW, { sight: null, fetcher: today });
  assert.deepEqual(today.asked, [], "claimed today already");
  const later = server();
  await peekNamed(bindings, id, TOMORROW, { sight: null, fetcher: later });
  assert.ok(later.asked.length > 0);
  assert.equal(peeks(bindings)[0].server !== null, true, "kept this time");
});

test("a file listed but not in the store, its keeping cut off, is put there when next fetched", async () => {
  const bindings = env();
  await results(bindings, [OFFER], { address: "192.0.2.1", asn: 64501 });
  const [{ object }] = rows(bindings, "SELECT object FROM readouts");
  bindings.READOUTS.objects.delete(`readouts/firmware/${sha(APP)}`);
  await results(bindings, [OFFER], { address: "203.0.113.3", asn: 64503 });
  const mark = bindings.READOUTS.objects.get(`readouts/firmware/${sha(APP)}`);
  assert.ok(mark, "put again");
  assert.equal(mark.options.customMetadata.readout, object, "under the readout that lists it");
  await countersMatch(bindings);
});

test("rejecting a link notes it apart in the same batch, or does neither", async () => {
  const bindings = env();
  const id = await pending(bindings);
  const batch = bindings.DB.batch;
  bindings.DB.batch = async () => { throw new Error("D1 is down"); };
  await assert.rejects(rejectLink(bindings.DB, id, sha(APP), NOW));
  bindings.DB.batch = batch;
  assert.deepEqual([linkRows(bindings)[0].review, rows(bindings, "SELECT * FROM rejected_links").length], ["pending", 0]);
  assert.equal(await rejectLink(bindings.DB, id, sha(APP), NOW), true);
  assert.equal(await rejectLink(bindings.DB, id, sha(APP), NOW), false, "not pending: nothing more");
  assert.equal(rows(bindings, "SELECT * FROM rejected_links").length, 1);
});

test("a fetch is given up after OFFER_FETCH_TIMEOUT_MS, and a timeout keeps nothing", async () => {
  assert.equal(OFFER_FETCH_TIMEOUT_MS, 20_000);
  const bindings = env();
  const signals = [];
  const slow = async (url, init = {}) => {
    signals.push(init.signal);
    throw new DOMException("timed out", "TimeoutError");
  };
  assert.deepEqual(await results(bindings, [OFFER], { fetcher: slow }), ["taken"]);
  assert.equal(signals.length, 1);
  assert.ok(signals[0] instanceof AbortSignal, "every fetch has a deadline");
  assert.deepEqual(rows(bindings, "SELECT why FROM offer_fetch_failures"), [{ why: "none" }]);
});

test("a file's names are kept whole, NAMES_KEPT_MOST characters at most, within R2's 2 KB of metadata", async () => {
  assert.equal(NAMES_KEPT_MOST, 1024);
  const many = Array.from({ length: 200 }, (_, n) => `FWSW-${String(n).padStart(3, "0")}-${"X".repeat(20)}`);
  const kept = namesKept(many);
  assert.ok(kept.length <= NAMES_KEPT_MOST);
  assert.deepEqual(kept.split("+"), many.slice(0, kept.split("+").length), "whole names, in order");
  assert.equal(namesKept([]), null);
  const file = Buffer.concat([wrapped(many), Buffer.from("the app")]);
  const bindings = env();
  const fetcher = cloud({ [URL_OF(PATH)]: file });
  await results(bindings, [{ ...OFFER, files: [{ ...FILE, sha256: sha(file) }] }], { fetcher });
  const { customMetadata } = bindings.READOUTS.objects.get(`readouts/firmware/${sha(file)}`).options;
  assert.equal(customMetadata.names, kept);
  assert.ok(JSON.stringify(customMetadata).length < 2048);
});

test("an offer's device, if a tool sends one, is neither checked nor kept", async () => {
  const bindings = env();
  assert.deepEqual(await results(bindings, [{ ...OFFER, device: "anything at all" }]), ["taken"]);
  const [{ object }] = rows(bindings, "SELECT object FROM readouts");
  assert.ok(!bindings.READOUTS.objects.get(object).body.includes("anything at all"));
});

test("links to review are listed those from the most networks first, then the longest waiting", async () => {
  const bindings = env();
  // One from two networks, the day before; one from one network, today.
  for (const [address, asn] of SENDERS.slice(0, 2)) {
    await results(bindings, [reviewOffer(1)], { address, asn, fetcher: server(), now: YESTERDAY });
  }
  await results(bindings, [reviewOffer(2)], { address: "203.0.113.3", asn: 64503, fetcher: server() });
  assert.deepEqual((await linksToReview(bindings.DB)).map((row) => [row.path, row.networks]),
    [["/ota/s61/r1.rbl", 2], ["/ota/s61/r2.rbl", 1]]);
});
