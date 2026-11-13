// admin/emulator.js: the suites' emulator runs - taken with the upload
// application's service token alone, held to what a run has, and shown on
// the view.
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { test } from "node:test";
import * as admin from "../admin/index.js";
import { EMULATOR_CAUSES, MAX_RUNS, refused, RUNS_PATH, SCOPE } from "../admin/emulator.js";
import { d1 } from "../api/local-d1.mjs";

const TEAM = "liberator";
const AUD = "a".repeat(64);
const UPLOAD_AUD = "c".repeat(64);
const NOW = Date.parse("2026-11-13T09:00:00Z");

const { privateKey, publicKey } = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true, ["sign", "verify"]);
const jwk = { ...(await crypto.subtle.exportKey("jwk", publicKey)), kid: "k1", alg: "RS256" };
const keys = async () => new Response(JSON.stringify({ keys: [jwk] }));
const b64 = (value) => Buffer.from(JSON.stringify(value)).toString("base64url");

async function token(claims) {
  const head = b64({ alg: "RS256", kid: "k1" });
  const body = b64({ iss: `https://${TEAM}.cloudflareaccess.com`, exp: NOW / 1000 + 600, ...claims });
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", privateKey, new TextEncoder().encode(`${head}.${body}`));
  return `${head}.${body}.${Buffer.from(signature).toString("base64url")}`;
}
// What Access passes on for the suites' service token, and for the maintainer.
const service = () => token({ aud: [UPLOAD_AUD], common_name: "abc123.access" });
const maintainer = () => token({ aud: [AUD], email: "owner@example.com" });

const env = () => ({ DB: d1(), ACCESS_TEAM: TEAM, ACCESS_AUD: AUD, ACCESS_UPLOAD_AUD: UPLOAD_AUD });

export function run(more = {}) {
  return {
    id: randomBytes(16).toString("hex"), finished: "2026-11-13T08:00:00Z", batch: "0123456789abcdef",
    suite: "fault", family: "espressif", device: "sonoff-s60", scenario: "sonoff>tasmota:D4:deviceoff-1",
    route: "sonoff>tasmota", step: "D4", fault: "deviceoff", at: 40, verdict: "PASS", tries: 2, seconds: 312,
    cloud: "stub", tool: "3.0.0", revision: "fd341cb23abc", dirty: false, firmware: { tasmota: "16.0.0" },
    workers: 6, ...more,
  };
}

const send = async (jwt, body, init = {}) => admin.handle(new Request(`https://admin.firmwareliberator.com${RUNS_PATH}`, {
  method: "POST", body: typeof body === "string" ? body : JSON.stringify(body), ...init,
  headers: { "Content-Type": "application/json", ...(jwt ? { "Cf-Access-Jwt-Assertion": jwt } : {}), ...(init.headers ?? {}) },
}), init.bindings ?? env(), NOW, keys);
const rows = (bindings) => bindings.DB.db.prepare("SELECT * FROM emulator_runs ORDER BY finished").all();

test("the suites' service token sends runs, each taken once however often it is sent", async () => {
  const bindings = env();
  const one = run();
  for (let time = 0; time < 2; time++) {
    const response = await send(await service(), { runs: [one, run({ verdict: "FAIL-SCREEN", tries: 4 })] }, { bindings });
    assert.equal(response.status, 200);
    const answer = await response.json();
    assert.equal(answer.taken.length, 2);
    assert.deepEqual(answer.refused, []);
  }
  const kept = rows(bindings);
  assert.equal(kept.length, 3, "the same run sent again is not counted again");
  const first = kept.find((row) => row.id === one.id);
  assert.equal(first.firmware, '{"tasmota":"16.0.0"}');
  assert.equal(first.dirty, 0);
  assert.equal(first.cause, null);
});

test("nothing but the upload application's service token sends runs", async () => {
  const bindings = env();
  for (const [why, jwt] of [
    ["no token", null],
    ["the maintainer's sign-in to the view", await maintainer()],
    ["the upload application's, but a person's", await token({ aud: [UPLOAD_AUD], email: "owner@example.com", common_name: "x" })],
    ["the view's audience with a service token", await token({ aud: [AUD], common_name: "abc123.access" })],
  ]) {
    assert.equal((await send(jwt, { runs: [run()] }, { bindings })).status, 403, why);
  }
  const unset = { ...env(), ACCESS_UPLOAD_AUD: "" };
  assert.equal((await send(await service(), { runs: [run()] }, { bindings: unset })).status, 403,
    "with no upload application set, none");
  assert.equal(rows(bindings).length, 0);
});

test("everything under the suites' path is held to their token alone, the runs' path and any other", async () => {
  assert.equal(RUNS_PATH, "/v1/emulator/runs");
  for (const path of [SCOPE, `${SCOPE}/`, `${SCOPE}/later`]) {
    const ask = async (jwt) => (await admin.handle(new Request(`https://admin.firmwareliberator.com${path}`, {
      method: "POST", headers: { "Cf-Access-Jwt-Assertion": jwt } }), env(), NOW, keys)).status;
    assert.equal(await ask(await maintainer()), 403, `${path}: not the maintainer's sign-in`);
    assert.equal(await ask(await service()), 404, `${path}: nothing there yet`);
  }
});

test("the service token reaches nothing of the view", async () => {
  for (const [path, method] of [["/", "GET"], ["/held", "POST"], ["/settings", "POST"], ["/emulator-cause", "POST"]]) {
    const response = await admin.handle(new Request(`https://admin.firmwareliberator.com${path}`, {
      method, headers: { "Cf-Access-Jwt-Assertion": await service(), Origin: "https://admin.firmwareliberator.com" },
    }), env(), NOW, keys);
    assert.equal(response.status, 403, path);
  }
});

test("runs are taken only as the suites send them", async () => {
  const jwt = await service();
  assert.equal((await send(jwt, "not json")).status, 400);
  assert.equal((await send(jwt, { runs: [] })).status, 400);
  assert.equal((await send(jwt, { runs: Array.from({ length: MAX_RUNS + 1 }, () => run()) })).status, 400);
  assert.equal((await send(jwt, { runs: [run()], more: 1 })).status, 400);
  assert.equal((await send(jwt, { runs: [run()] }, { headers: { "Content-Type": "text/plain" } })).status, 415);
  const get = await admin.handle(new Request(`https://admin.firmwareliberator.com${RUNS_PATH}`, {
    headers: { "Cf-Access-Jwt-Assertion": jwt } }), env(), NOW, keys);
  assert.equal(get.status, 405);
});

test("a run not as a run is refused alone, with why; the rest are taken", async () => {
  const bindings = env();
  const bad = [
    run({ id: "x" }), run({ verdict: "pass" }), run({ suite: "other" }), run({ cloud: "elsewhere" }),
    run({ at: 101 }), run({ tries: -1 }), run({ dirty: 1 }), run({ firmware: ["16.0.0"] }), run({ revision: "HEAD" }),
    run({ host: "my-machine" }), run({ finished: "2026-11-14T12:00:00Z" }), run({ finished: "2026-08-01T00:00:00Z" }),
    run({ scenario: "é" }), run({ family: "Espressif" }),
  ];
  const { step, ...noStep } = run();
  const answer = await (await send(await service(), { runs: [run(), ...bad, noStep] }, { bindings })).json();
  assert.equal(answer.taken.length, 1);
  assert.equal(answer.refused.length, bad.length + 1);
  assert.match(answer.refused.find((one) => one.id === noStep.id).why, /step/);
  assert.match(answer.refused.at(-2).why, /family/);
  assert.match(bad.map((one) => refused(one, NOW)).join("\n"), /fields no run has: host/);
  assert.equal(rows(bindings).length, 1);
});

test("a run of a family or device the view has never seen is taken: a new one needs no change here", () => {
  assert.equal(refused(run({ family: "realtek", device: "sonoff-new-thing", suite: "attended", route: null, step: null,
    fault: null, at: null, tries: null }), NOW), null);
});

const view = async (bindings) => admin.page(bindings, { email: "owner@example.com" }, new Date(NOW), keys);
const section = (html, id) => html.split(`id="${id}">`)[1].split("</section>")[0];

test("the view shows the runs: totals, by revision, by device, fault by step, and what failed", async () => {
  const bindings = env();
  const scenario = "sonoff>tasmota:D4:deviceoff-1";
  const runs = [
    run({ scenario, revision: "1111111", finished: "2026-11-11T10:00:00Z", seconds: 3600 }),
    run({ scenario, revision: "2222222", finished: "2026-11-12T10:00:00Z", verdict: "FAIL", tries: 4 }),
    run({ scenario, revision: "2222222", finished: "2026-11-12T11:00:00Z" }),
    run({ family: "beken", device: "sonoff-s61", scenario: "sonoff>openbeken:apply:deviceoff-1@10", step: "apply",
      firmware: { openbeken: "1.18.320-2" }, verdict: "BRICK", revision: "2222222", finished: "2026-11-12T12:00:00Z" }),
    run({ suite: "attended", scenario: "stray-keys", route: null, step: null, fault: null, at: null, tries: null,
      verdict: "FAIL-SCREEN", cloud: "stub", finished: "2026-11-12T13:00:00Z", revision: "2222222" }),
  ];
  await send(await service(), { runs }, { bindings });
  const html = await view(bindings);
  const top = section(html, "emulator");
  assert.match(top, /Runs<\/div><div class="tile-value">5</);
  assert.match(top, /Bricked<\/div><div class="tile-value">1</);
  assert.match(top, /Failures not marked<\/div><div class="tile-value">3</);
  const revisions = section(html, "emulator-revisions");
  assert.match(revisions, /<td>2222222<\/td><td>3\.0\.0<\/td><td class="number">4<\/td><td class="number">3<\/td><td>25%<\/td><td class="number">1<\/td>/,
    "a scenario that passed on the revision before and fails on this is a new failure");
  assert.match(section(html, "emulator-devices"), /<td>beken<\/td><td>sonoff-s61<\/td><td>fault<\/td>[^]*openbeken 1\.18\.320-2/);
  assert.match(section(html, "emulator-faults"), /<th>D4<\/th><td class="number heat"[^>]*>1\/3<\/td>/);
  assert.match(section(html, "emulator-flaky"), /sonoff&gt;tasmota:D4:deviceoff-1/);
  const failures = section(html, "emulator-failures");
  assert.match(failures, /FAIL \(new\)/);
  assert.equal((failures.match(/<select name="cause"/g) ?? []).length, 3);
});

test("a failure's cause is marked from the view's own form, a passing run never", async () => {
  const bindings = env();
  const failed = run({ verdict: "FAIL" });
  const passed = run();
  await send(await service(), { runs: [failed, passed] }, { bindings });
  const mark = async (id, cause, origin = "https://admin.firmwareliberator.com") => admin.handle(
    new Request("https://admin.firmwareliberator.com/emulator-cause", {
      method: "POST", body: new URLSearchParams({ id, cause }),
      headers: { "Cf-Access-Jwt-Assertion": await maintainer(), Origin: origin } }), bindings, NOW, keys);
  assert.equal((await mark(failed.id, "tool", "https://elsewhere.example")).status, 403);
  assert.equal((await mark(failed.id, "nonsense")).status, 400);
  assert.equal((await mark(failed.id, "tool")).status, 303);
  assert.equal((await mark(passed.id, "tool")).status, 303);
  const causes = Object.fromEntries(rows(bindings).map((row) => [row.id, row.cause]));
  assert.deepEqual(causes, { [failed.id]: "tool", [passed.id]: null });
  assert.match(section(await view(bindings), "emulator"), /Bugs found<\/div><div class="tile-value">1</);
  assert.equal((await mark(failed.id, "")).status, 303, "and cleared");
  assert.equal(rows(bindings).find((row) => row.id === failed.id).cause, null);
  assert.ok(EMULATOR_CAUSES.includes("tool"));
});
