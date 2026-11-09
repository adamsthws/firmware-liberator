// The API Worker (index.js) on this computer, for emulator runs that send
// their readouts (tools/espressif/emulator/unattended-test.sh --api): the
// Worker's own code, with an R2 bucket that keeps what it is given in a folder
// and a rate limiter holding READOUT_LIMIT's rule - two a minute from one
// address, in a fixed minute's window. Never deployed.
//   node website/api/local.mjs ADDRESS PORT FOLDER [ENOUGH]
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import worker from "./index.js";

const [address, port, folder, enough = ""] = process.argv.slice(2);
fs.mkdirSync(folder, { recursive: true });
const file = (key) => path.join(folder, key.replaceAll("/", "__"));

const READOUTS = {
  head: async (key) => (fs.existsSync(file(key)) ? { key } : null),
  get: async (key) => (fs.existsSync(file(key)) ? { text: async () => fs.readFileSync(file(key), "utf8") } : null),
  list: async ({ prefix }) => ({
    objects: fs.readdirSync(folder).map((name) => name.replaceAll("__", "/")).filter((key) => key.startsWith(prefix))
      .map((key) => ({ key, size: fs.statSync(file(key)).size })),
  }),
  put: async (key, body, options) => {
    fs.writeFileSync(file(key), typeof body === "string" ? body : Buffer.from(body));
    fs.writeFileSync(`${file(key)}.meta.json`, JSON.stringify(options ?? {}));
  },
};
const windows = new Map();
const READOUT_LIMIT = {
  limit: async ({ key }) => {
    const minute = Math.floor(Date.now() / 60_000);
    const seen = windows.get(key);
    const count = seen?.minute === minute ? seen.count + 1 : 1;
    windows.set(key, { minute, count });
    return { success: count <= 2 };
  },
};
const env = { READOUTS, READOUT_LIMIT, ENOUGH: enough };

// Gone once the script that started it is (PARENT, its process id), however it ended.
if (process.env.PARENT) {
  setInterval(() => {
    try {
      process.kill(Number(process.env.PARENT), 0);
    } catch {
      process.exit(0);
    }
  }, 2000).unref();
}

const server = http.createServer(async (request, response) => {
  const chunks = [];
  for await (const chunk of request) chunks.push(chunk);
  const body = Buffer.concat(chunks);
  const headers = new Headers(request.headers);
  headers.set("CF-Connecting-IP", request.socket.remoteAddress || "unknown");
  const answer = await worker.fetch(new Request(`https://api.firmwareliberator.com${request.url}`, {
    method: request.method, headers, body: ["GET", "HEAD"].includes(request.method) ? undefined : body,
  }), env);
  const text = await answer.text();
  fs.appendFileSync(path.join(folder, "requests.log"),
    `${new Date().toISOString()} ${request.method} ${request.url} ${body.length} -> ${answer.status} ${text}\n`);
  response.writeHead(answer.status, Object.fromEntries(answer.headers));
  response.end(text);
});
server.listen(Number(port), address, () =>
  console.log(`API on ${address}:${server.address().port}, kept in ${folder}, ENOUGH=${enough}`));
