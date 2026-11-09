// api.firmwareliberator.com: the project's API. For now one path, /readout,
// where the tool sends a device's readout with its owner's consent (tools/autoflash/readout.py, .github/readouts.md). A Worker
// of its own, apart from the website's, so nothing sent here can touch the site.
//
// The tool is open source, so it can hold no secret: what keeps this from being
// flooded is that each readout costs its sender work, and that the bucket has
// hard limits it is never let past.
// - A proof of work: SHA-256 of "<time>:<the body's SHA-256>:<nonce>" starting
//   with PROOF_BITS zero bits, about 17 million tries (10 to 30 seconds in the
//   tool's Python, once a readout), made within PROOF_AGE of now and good for
//   that body alone. Hardware made for it does that in milliseconds, so the
//   daily limits below, not this, are what bound the bucket.
// - It must be a readout: a tar of at most MAX_BYTES, its first file a
//   manifest.json of the format the tool writes.
// - At most RATE_LIMIT a minute from one address (held by the limiter, never
//   stored), and DAILY_COUNT readouts and DAILY_BYTES a day from everyone.
// - Kept RETENTION_DAYS: the bucket's lifecycle rule (tools/repo/
//   cloudflare_settings.py) deletes everything under readouts/. One worth
//   keeping is moved under kept/ by hand. So the bucket stays under R2's free
//   10 GB: DAILY_BYTES * RETENTION_DAYS, plus KEPT_BYTES for kept/.
// - A device enough readouts of are in, by eWeLink's product model in ENOUGH
//   (a comma-separated variable in wrangler.jsonc), is thanked and not stored.
// - The same readout sent again the same day is the one already stored.
// No address or other detail of the sender is kept.

export const MAX_BYTES = 24 * 1024 * 1024;
export const PROOF_BITS = 24;
export const PROOF_AGE = 15 * 60;
export const DAILY_COUNT = 100;
export const DAILY_BYTES = 250 * 1024 * 1024;
export const RETENTION_DAYS = 30;
export const KEPT_BYTES = 2 * 1024 * 1024 * 1024;
export const FORMAT = 1;

export default {
  async fetch(request, env) {
    const path = new URL(request.url).pathname;
    if (path === "/readout") {
      return readout(request, env);
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
// `bits` is PROOF_BITS but in the tests, which ask for less.
export async function proven(proof, digest, now = Date.now() / 1000, bits = PROOF_BITS) {
  const [time, nonce, extra] = String(proof || "").split(":");
  if (extra !== undefined || !/^\d{1,12}$/.test(time || "") || !/^\d{1,20}$/.test(nonce || "")) {
    return false;
  }
  if (Math.abs(now - Number(time)) > PROOF_AGE) {
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
  const enough = String(env.ENOUGH || "").split(",").map((model) => model.trim().toUpperCase()).filter(Boolean);
  if (enough.includes(manifest.product.toUpperCase())) {
    return answer(200, { enough: true });
  }
  const day = now.toISOString().slice(0, 10);
  const id = `${day}-${digest.slice(0, 12)}`;
  const key = `readouts/${day}/${digest.slice(0, 24)}.tar`;
  if (await env.READOUTS.head(key)) {
    return answer(200, { id });
  }
  const today = await env.READOUTS.list({ prefix: `readouts/${day}/`, limit: 1000 });
  const stored = today.objects.reduce((total, item) => total + item.size, 0);
  if (today.objects.length >= DAILY_COUNT || stored + body.byteLength > DAILY_BYTES) {
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
  return answer(201, { id });
}
