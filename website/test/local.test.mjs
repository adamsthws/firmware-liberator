// api/local.mjs: the API Worker's own code on this computer, for emulator runs
// that send their readouts (unattended-test.sh --api).
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

const SERVER = new URL("../api/local.mjs", import.meta.url).pathname;

function proofFor(body, bits) {
  const time = Math.floor(Date.now() / 1000);
  const digest = createHash("sha256").update(body).digest("hex");
  for (let nonce = 0; ; nonce += 1) {
    const hash = createHash("sha256").update(`${time}:${digest}:${nonce}`).digest();
    if (hash.readUInt32BE(0) >>> (32 - bits) === 0) return `${time}:${nonce}`;
  }
}

async function started(folder, enough, env = {}) {
  const child = spawn(process.execPath, [SERVER, "127.0.0.1", "0", folder, enough], { env: { ...process.env, ...env } });
  // Port 0 is the system's choice: read it back from what the server says.
  const port = await new Promise((resolve, reject) => {
    child.stdout.on("data", (data) => {
      const found = /:(\d+),/.exec(String(data));
      if (found) resolve(Number(found[1]));
    });
    child.on("exit", (code) => reject(new Error(`exited ${code}`)));
  });
  return { child, port };
}

test("it answers as the Worker does and keeps what it is given in its folder", async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "autoflash-api-"));
  const { child, port } = await started(folder, "NSPANEL");
  try {
    const body = Buffer.from(JSON.stringify({ format: 1, model: "NSPanel", stock_model: "E32-MSW-NX", stock_version: "1.5.0",
      stage: "first", firmware: [] }));
    const answer = await fetch(`http://127.0.0.1:${port}/v1/wanted`, { method: "POST", body,
      headers: { "Content-Type": "application/json", "X-Proof": proofFor(body, 20) } });
    assert.deepEqual(await answer.json(), { wanted: false, why: "enough", held: [], send: "files" });
    assert.match(fs.readFileSync(path.join(folder, "requests.log"), "utf8"), /POST \/v1\/wanted .* -> 200/);
  } finally {
    child.kill();
    fs.rmSync(folder, { recursive: true, force: true });
  }
});

test("it fetches no offer's files from eWeLink's own server", async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "autoflash-api-"));
  const { child, port } = await started(folder, "", { AUTOFLASH_LOCAL_OTA_BASE: "" });
  try {
    const answer = await fetch(`http://127.0.0.1:${port}/local/retry-offers`, { method: "POST" });
    assert.equal(answer.status, 409);
    assert.match((await answer.json()).error, /AUTOFLASH_LOCAL_OTA_BASE/);
  } finally {
    child.kill();
    fs.rmSync(folder, { recursive: true, force: true });
  }
});

test("it is gone once the script that started it is", async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "autoflash-api-"));
  const parent = spawn("sleep", ["30"]);
  const { child } = await started(folder, "", { PARENT: String(parent.pid) });
  try {
    parent.kill();
    const code = await new Promise((resolve) => child.on("exit", resolve));
    assert.equal(code, 0);
  } finally {
    child.kill();
    fs.rmSync(folder, { recursive: true, force: true });
  }
});

// The cloud stub both emulators run (tools/ewelink/stub_cloud/stub_server.py),
// in this computer's Python, offering a file at a link of eWeLink's shape and
// serving it over plain HTTP: what it says is {port, files}, the files as the
// tool sends their links (offered_firmware.links).
const STUB = `
import json
from tools.ewelink import offered_firmware
from tools.ewelink.stub_cloud import stub_server as ss
from tools.ewelink.stub_cloud.firmware import Update
ss.log = lambda *parts: None
stub = ss.Handler.stub = ss.Stub("127.0.0.1", 443, 10, {"deviceid": "1000abcdef", "devicekey": "k"},
                                 Update("1.2.0", b"the stub's offered file"))
server = ss.PlainServer(("127.0.0.1", 0), ss.Handler)
asked = json.dumps({"deviceInfoList": [{"deviceid": "1000abcdef", "model": "m", "version": "0.0.1"}]}).encode()
files = offered_firmware.links(stub.ota_query(asked)["data"]["otaInfoList"])
print(json.dumps({"port": server.server_address[1], "files": files}), flush=True)
server.serve_forever()
`;

test("sent as links, an offer's files are fetched from the cloud stub once one is taken as right", async () => {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "autoflash-api-"));
  const stub = spawn(process.env.AUTOFLASH_PYTHON || "python3", ["-c", STUB],
    { cwd: new URL("../../", import.meta.url).pathname });
  let child;
  try {
    const { port: stubPort, files } = await new Promise((resolve, reject) => {
      stub.stdout.once("data", (data) => resolve(JSON.parse(String(data))));
      stub.on("exit", (code) => reject(new Error(`the stub exited ${code}`)));
    });
    let port;
    ({ child, port } = await started(folder, "", { AUTOFLASH_LOCAL_PROOF_BITS: "8",
      AUTOFLASH_LOCAL_OFFERED_SEND: "links", AUTOFLASH_LOCAL_OTA_BASE: `http://127.0.0.1:${stubPort}` }));
    const body = Buffer.from(JSON.stringify({ format: 1, tool_version: "3.1.0", cloud: "ewelink", offers: [
      { model: "S60TPF", stock_model: "SN-ESP32C3-S60-01", stock_version: "1.2.0", device: null, files }] }));
    const sent = await fetch(`http://127.0.0.1:${port}/v1/offers`, { method: "POST", body,
      headers: { "Content-Type": "application/json", "X-Proof": proofFor(body, 8) } });
    assert.deepEqual(await sent.json(), { send: "links", offers: [{ result: "taken" }] });
    const kept = fs.readdirSync(folder).find((name) => /^readouts__\d{4}-\d{2}-\d{2}__[0-9a-f]{24}\.json$/.test(name));
    assert.equal(JSON.parse(fs.readFileSync(path.join(folder, kept), "utf8")).offered[0].link, files[0].path);
    // Here no request names its network, so none is agreed: approved, as the maintainer would.
    const db = new (await import("node:sqlite")).DatabaseSync(path.join(folder, "api.sqlite"));
    db.prepare("UPDATE readouts SET review = 'approved' WHERE stage = 'first'").run();
    db.close();
    const retried = await fetch(`http://127.0.0.1:${port}/local/retry-offers`, { method: "POST" });
    assert.deepEqual(await retried.json(), { offers: 1, fetched: 1, missing: 0 });
    const held = path.join(folder, `readouts__firmware__${files[0].sha256}`);
    assert.equal(fs.readFileSync(held, "utf8"), "the stub's offered file");
    assert.deepEqual(JSON.parse(fs.readFileSync(`${held}.meta.json`, "utf8")).customMetadata,
      { readout: kept.replaceAll("__", "/"), fetched: "true" });
  } finally {
    child?.kill();
    stub.kill();
    fs.rmSync(folder, { recursive: true, force: true });
  }
});
