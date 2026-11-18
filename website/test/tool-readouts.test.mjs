// The tool's own offers and readouts (tools/autoflash/readout.py, offers.py's
// sending), sent to the API Worker's own code (api/local.mjs) taking links, as
// a run sends them: the questions, the offers and a whole readout this
// computer's Python makes, against the API as it answers, so the two sides
// cannot drift apart unseen - a device with no product model, say, or a
// sender's own offer turning down its whole readout (THEIRS). Its offered
// files are fetched from the cloud stub both emulators run. Skipped where
// this computer has no Python, but never in CI, whose runner has one.
import assert from "node:assert/strict";
import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

const ROOT = new URL("../../", import.meta.url).pathname;
const SERVER = new URL("../api/local.mjs", import.meta.url).pathname;
const PYTHON = process.env.AUTOFLASH_PYTHON || "python3";
const BITS = 8;
const NO_PYTHON = spawnSync(PYTHON, ["--version"]).error && !process.env.CI ? `no ${PYTHON} on this computer` : false;

// The cloud stub (tools/ewelink/stub_cloud/stub_server.py), offering a file
// at a link of eWeLink's shape and serving it over plain HTTP: what it says is
// {port, offers}, the offers as eWeLink lists them (otaInfoList).
const STUB = `
import json
from tools.ewelink.stub_cloud import stub_server as ss
from tools.ewelink.stub_cloud.firmware import Update
ss.log = lambda *parts: None
stub = ss.Handler.stub = ss.Stub("127.0.0.1", 443, 10, {"deviceid": "1000abcdef", "devicekey": "k"},
                                 Update("1.2.0", b"the stub's offered file"))
server = ss.PlainServer(("127.0.0.1", 0), ss.Handler)
asked = json.dumps({"deviceInfoList": [{"deviceid": "1000abcdef", "model": "m", "version": "0.0.1"}]}).encode()
print(json.dumps({"port": server.server_address[1], "offers": stub.ota_query(asked)["data"]["otaInfoList"]}), flush=True)
server.serve_forever()
`;

// The tool's own code, one step a run: "sends" (website_sends), "offer" and
// "nameless" (an_offer and send_offers, the latter a device eWeLink names no
// product model of), "wanted" (ask_wanted, a whole readout's question), and
// "whole" (readout.run of an S60 read in full: the device, eWeLink and the
// read stood in for, the question, packing and sending its own). The last
// line it prints is what it came to, as JSON.
const RUN = `
import json, sys, tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from tools.autoflash import account, hostsys, kinds, openbeken, output, prompts, readout, readout_espressif
from tools.autoflash import records, report_upload, runs
from tools.autoflash.report_data import device_hash
from tools.autoflash.state import RunState
from tools.ewelink import firmware_name

step, offers, bits = sys.argv[1], json.loads(sys.argv[2]), int(sys.argv[3])
S60, MAC, VERSION = kinds.SONOFF_S60, "12:34:56:ab:cd:ef", "1.0.0"
quiet = [mock.patch.object(readout, "PROOF_BITS", bits), mock.patch.object(readout, "LIGHT_PROOF_BITS", bits)]
for patch in quiet:
    patch.start()
if step == "sends":
    said = {"sends": readout.website_sends()}
elif step in ("offer", "nameless"):
    product, model = ("S60TPF", S60.stock_model) if step == "offer" else ("", "SN-NO-PRODUCT-01")
    sends, results = readout.send_offers([readout.an_offer(product, model, VERSION, offers, device_hash(MAC))])
    said = {"sends": sends, "results": results}
elif step == "wanted":
    answer = readout.ask_wanted("S60TPF", S60.stock_model, VERSION, "whole", readout.offered_digests(offers),
                                device_hash(MAC))
    said = {"wanted": answer.wanted, "why": answer.why, "held": sorted(answer.held), "send": answer.send}
else:
    work = Path(tempfile.mkdtemp())
    ctx = SimpleNamespace(state=RunState.open(work / "run"), dir=work, listen_ip=lambda: "192.0.2.141",
                          email=lambda: "", ewelink_env=lambda: {}, helper_env=lambda: {},
                          ewelink_region=lambda: "eu")
    device = {"deviceid": "1000abcdef", "name": "Hall", "productModel": "S60TPF", "brandName": "SONOFF",
              "extra": {"model": S60.stock_model, "uiid": 190}, "params": {"fwVersion": VERSION, "staMac": MAC}}
    asked = account.Asked(VERSION, S60.stock_model, "S60TPF", [], {"offers": offers}, "1000abcdef")
    flash = {"flash.bin": bytes(0x400000), "efuse.bin": bytes(124), "info.txt": b"bridge\\n", "report.json": b"{}"}

    def read(ctx, manifest, *_args, **_kwargs):
        """The device's memory read, as readout_espressif marks a manifest that holds it."""
        manifest.update(read=True, flash={"size": len(flash["flash.bin"]), "matches_device": True})
        return flash, True
    for patch in (mock.patch.object(readout, "TO_SEND", work / "readouts-to-send"),
                  mock.patch.object(readout, "locate", return_value="192.0.2.77"),
                  mock.patch.object(readout_espressif.installer, "bridge_answers", return_value=False),
                  mock.patch.object(openbeken, "is_readout", return_value=False),
                  mock.patch.object(account, "already_converting", return_value=None),
                  mock.patch.object(account, "ask_account", return_value=asked),
                  mock.patch.object(hostsys, "mac_of", return_value=MAC),
                  mock.patch.object(firmware_name, "learn",
                                    return_value=(firmware_name.Learned(S60.wrapper_model, "ESP32C3", "eWeLink"), [])),
                  mock.patch.object(readout_espressif, "read_and_anonymise", read),
                  mock.patch.object(records, "remember_device"), mock.patch.object(runs, "file_device"),
                  mock.patch.object(report_upload, "begin_readout"), mock.patch.object(report_upload, "send_readout"),
                  mock.patch.object(prompts, "section"), mock.patch.object(output, "say"),
                  mock.patch.object(output, "log_only"), mock.patch.object(output, "stage_begin"),
                  mock.patch.object(output, "stage_end")):
        patch.start()
    said = {"said": readout.run(ctx, device), "outcome": ctx.readout.outcome, "reference": ctx.readout.detail,
            "whole": ctx.readout.whole, "kept": sorted(path.name for path in work.glob("*.tar"))}
print(json.dumps(said))
`;

function tool(step, url, offers) {
  // Where readouts go, as a test's own server; no report of its own sent anywhere.
  const env = { ...process.env, AUTOFLASH_READOUT_URL: url, AUTOFLASH_NO_REPORT: "1" };
  delete env.AUTOFLASH_EMULATOR_URL;
  const done = spawnSync(PYTHON, ["-c", RUN, step, JSON.stringify(offers), String(BITS)],
    { cwd: ROOT, env, encoding: "utf8", timeout: 120_000 });
  assert.equal(done.status, 0, done.stderr);
  return JSON.parse(done.stdout.trim().split("\n").at(-1));
}

// The stub, then the API taking links and fetching from it, each its own
// process, both gone after `body` however it ends.
async function withApi(body) {
  const folder = fs.mkdtempSync(path.join(os.tmpdir(), "autoflash-api-"));
  const stub = spawn(PYTHON, ["-c", STUB], { cwd: ROOT });
  let api;
  try {
    const { port: stubPort, offers } = await new Promise((resolve, reject) => {
      stub.stdout.once("data", (data) => resolve(JSON.parse(String(data))));
      stub.on("exit", (code) => reject(new Error(`the stub exited ${code}`)));
    });
    api = spawn(process.execPath, [SERVER, "127.0.0.1", "0", folder], { env: { ...process.env,
      AUTOFLASH_LOCAL_PROOF_BITS: String(BITS), AUTOFLASH_LOCAL_OFFERED_SEND: "links",
      AUTOFLASH_LOCAL_OTA_BASE: `http://127.0.0.1:${stubPort}` } });
    const port = await new Promise((resolve, reject) => {
      api.stdout.on("data", (data) => {
        const found = /:(\d+),/.exec(String(data));
        if (found) resolve(Number(found[1]));
      });
      api.on("exit", (code) => reject(new Error(`the API exited ${code}`)));
    });
    const run = (step) => tool(step, `http://127.0.0.1:${port}/v1/readout`, offers);
    await body({ run, folder, sha: offers[0].binList[0].digest });
  } finally {
    api?.kill();
    stub.kill();
    fs.rmSync(folder, { recursive: true, force: true });
  }
}

const requests = (folder) => fs.readFileSync(path.join(folder, "requests.log"), "utf8");

// The offered file, held as the API keeps one fetched: by its SHA-256, which
// what was fetched must be, and naming the readout or offer that first named it.
function held(folder, sha) {
  const file = path.join(folder, `readouts__firmware__${sha}`);
  assert.ok(fs.existsSync(file), "the offered file is not held");
  assert.equal(createHash("sha256").update(fs.readFileSync(file)).digest("hex"), sha);
  return JSON.parse(fs.readFileSync(`${file}.meta.json`, "utf8")).customMetadata;
}

test("a sender's offers are taken, then its own whole readout of the device is wanted and taken",
  { skip: NO_PYTHON }, async () => {
    await withApi(async ({ run, folder, sha }) => {
      assert.deepEqual(run("sends"), { sends: "links" }, "an empty /offers answers links");
      assert.deepEqual(run("offer"), { sends: "links", results: [["taken", ""]] });
      // Fetched on first sight: no approval, no night.
      assert.equal(held(folder, sha).fetched, "true");
      assert.deepEqual(run("nameless"), { sends: "links", results: [["taken", ""]] }, "a product model of -");
      assert.deepEqual(run("wanted"), { wanted: true, why: "", held: [sha], send: "links" });
      const whole = run("whole");
      assert.deepEqual({ ...whole, reference: whole.reference.replace(/-[0-9a-f]{12}$/, "-ID") },
        { said: "", outcome: "accepted", reference: `${new Date().toISOString().slice(0, 10)}-ID`, whole: true,
          kept: [] });
      assert.match(requests(folder), /POST \/v1\/readout \d+ -> 201 \{"id":/);
    });
  });

test("a whole readout sent as links is taken, and its offered file fetched on first sight",
  { skip: NO_PYTHON }, async () => {
    await withApi(async ({ run, folder, sha }) => {
      const whole = run("whole");
      assert.equal(whole.outcome, "accepted");
      const kept = fs.readdirSync(folder).find((name) => /^readouts__\d{4}-\d{2}-\d{2}__[0-9a-f]{24}\.tar$/.test(name));
      assert.ok(kept, "the readout is not kept");
      assert.deepEqual(held(folder, sha), { readout: kept.replaceAll("__", "/"), fetched: "true" });
      assert.doesNotMatch(requests(folder), /\/v1\/offers/, "a whole readout sends no offer");
    });
  });
