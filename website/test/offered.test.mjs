// /v1/offers (api/index.js offers) and api/offered.js: first stages sent as
// links, and whole readouts' links, are held and agreed as first stages are,
// and the API fetches their files itself, on first sight and once an offer is
// taken as right, keeping each only if it is the file its SHA-256 names. No
// test reaches eWeLink: every fetch is given one made here.
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { test } from "node:test";
import { read } from "../api/counters.js";
import worker, * as api from "../api/index.js";
import { d1 } from "../api/local-d1.mjs";
import { CLOUDS, OFFER_FETCH_MAX_BYTES, OFFER_FETCH_PATHS, OFFER_FETCHES_DAILY, OFFER_FIRST_SIGHT_PER_SENDER, fetchOffer,
  firmwareNames, retryOffers } from "../api/offered.js";
import { PENDING, READOUTS_EVER } from "../api/pending.js";
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
    delete: async (key) => { objects.delete(key); },
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
const SENDERS = [["192.0.2.1", 64501], ["192.0.2.2", 64502], ["192.0.2.3", 64503]];
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
  assert.equal(await readout(bindings, "192.0.2.2", 64502), 201);
  assert.deepEqual(await results(bindings, [OFFER], { address: "192.0.2.1", asn: 64501 }), ["have"],
    "the same sender's first stage again, as links");
  const fetcher = cloud();
  assert.deepEqual(await results(bindings, [OFFER], { address: "192.0.2.3", asn: 64503, fetcher }), ["taken"]);
  assert.equal(rows(bindings, "SELECT * FROM agreed_offers").length, 1, "the third, as links, agrees with the two");
  assert.deepEqual(fetcher.asked, [], "the files held already, from the first stages that carried them");
});

test("a readout that takes an offer sent as links as right fetches its files", async () => {
  const bindings = env();
  // Not given on first sight, the day before.
  await send(bindings, [OFFER], { address: "192.0.2.1", asn: 64501, fetcher: cloud({}), now: YESTERDAY });
  await send(bindings, [OFFER], { address: "192.0.2.2", asn: 64502, fetcher: cloud({}), now: YESTERDAY });
  const fetcher = cloud();
  assert.equal(await readout(bindings, "192.0.2.3", 64503, { read: true, withFile: false }, fetcher), 201,
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
  const oneNetwork = [["198.51.100.1", 64510], ["198.51.100.2", 64510], ["198.51.100.3", 64510]];
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

test("files are fetched OFFER_FETCHES_DAILY a day at most, across everyone", async () => {
  assert.equal(OFFER_FETCHES_DAILY, 50);
  const bindings = env();
  bindings.DB.db.prepare("INSERT INTO counters (name, day, count) VALUES (?, ?, ?)")
    .run(`offer-fetches:${DAY}`, DAY, OFFER_FETCHES_DAILY - 1);
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
    { ...OFFER, device: "not a hash" },
    { ...OFFER, files: "user1.bin" },
    { ...OFFER, files: Array.from({ length: api.MAX_FIRMWARE + 1 }, (_, n) => ({ ...FILE, sha256: sha(String(n)) })) },
    { ...OFFER, files: [{ ...FILE, sha256: "A".repeat(64) }] },
    { ...OFFER, files: [{ ...FILE, version: undefined }] },
    { ...OFFER, files: [{ ...FILE, name: "" }] },
    { ...OFFER, files: [{ ...FILE, path: `http://192.0.2.1:8088${PATH}` }] },
    { ...OFFER, files: [{ ...FILE, path: "/ota/rom/short/user1.bin" }] },
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
      "Content-Type": "application/json", "Content-Length": String(body.length), "CF-Connecting-IP": "192.0.2.3",
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
  await send(bindings, [OFFER], { address: "192.0.2.2", asn: 64502 });
  assert.deepEqual(rows(bindings, "SELECT * FROM agreed_offers"), [], "two senders, not three");
  await send(bindings, [OFFER], { address: "192.0.2.3", asn: 64503 });
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
  assert.deepEqual(await results(firsts, [OFFER], { address: "192.0.2.2", asn: 64502 }), ["taken"]);
  assert.deepEqual(await results(firsts, [OFFER], { address: "192.0.2.2", asn: 64502 }), ["have"], "links, again");
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
  assert.deepEqual(await results(bindings, [OFFER], { address: "192.0.2.2", asn: 64502, fetcher }), ["taken"]);
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

test("first sight fetches within OFFER_FETCHES_DAILY, and notes no failure past it", async () => {
  const bindings = env();
  bindings.DB.db.prepare("INSERT INTO counters (name, day, count) VALUES (?, ?, ?)")
    .run(`offer-fetches:${DAY}`, DAY, OFFER_FETCHES_DAILY);
  const fetcher = cloud();
  assert.deepEqual(await results(bindings, [OFFER], { fetcher }), ["taken"]);
  assert.deepEqual(fetcher.asked, []);
  assert.deepEqual(rows(bindings, "SELECT * FROM offer_fetch_failures"), []);
  assert.deepEqual(await results(bindings, [OFFER], { address: "192.0.2.2", fetcher, now: TOMORROW }), ["taken"]);
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
    assert.deepEqual(await results(other, [six[5]], { address: "192.0.2.2", asn: 64502, fetcher: theirs }), ["taken"]);
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
