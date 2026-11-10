// Made-up public figures, for building /stats without the live ones: a few
// hundred made-up reports written as /reports writes them (api/reports.js),
// folded and summed by the real nightly job (api/nightly.js), whose snapshot
// this gives - so they always have the format stats.json has. The same each
// time (a seeded random), and nothing of a real run in them.
//   node website/scripts/sample-stats.mjs > /tmp/stats.json
//   STATS_FILE=/tmp/stats.json npm run build
import { readFileSync } from "node:fs";
import { d1 } from "../api/local-d1.mjs";
import { nightly, SNAPSHOT_KEY } from "../api/nightly.js";
import { writes } from "../api/reports.js";

const FIXTURE = JSON.parse(readFileSync(new URL("../test/fixtures/reports.json", import.meta.url), "utf8")).valid[0];

// A small seeded random (mulberry32), so the sample is the same every run.
function random(seed) {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const DEVICES = [
  // [model, device, family, share, success]
  ["S60TPG", "sonoff-s60", "espressif", 0.38, 0.9],
  ["S60TPF", "sonoff-s60", "espressif", 0.22, 0.88],
  ["POWR316D", "sonoff-pow-elite", "espressif", 0.1, 0.8],
  ["DUALR3", "sonoff-dualr3", "espressif", 0.08, 0.76],
  ["S61STPF", "sonoff-s61", "beken", 0.08, 0.7],
  ["BASIC-1GS", "sonoff-basic-1gs", "beken", 0.05, 0.72],
  ["BASICR4", null, null, 0.05, 0],
  ["NSPANEL", null, null, 0.04, 0],
];
const FAILURES = [["D4", "upload-cut"], ["E1", "unexpected-layout"], ["B2", "device-no-answer"], ["F4", "network-join"],
  ["A2", "ewelink-account"]];
const OS = [["windows", "11 (build 10.0.22631)"], ["macos", "15.1"], ["linux", "Ubuntu 24.04.1 LTS"]];

const dayOf = (date) => date.toISOString().slice(0, 10);

// The made-up database and the night's snapshot of it: {db, snapshot}.
export async function sampleDatabase(now = new Date("2026-12-01T03:17:00Z"), reports = 640) {
  const next = random(1794);
  const db = d1();
  const pick = (weights) => {
    let roll = next() * weights.reduce((sum, [, , , share]) => sum + share, 0);
    return weights.find(([, , , share]) => (roll -= share) <= 0) ?? weights[0];
  };
  const days = 120;
  for (let n = 0; n < reports; n += 1) {
    // More each month, as a tool found by more people would be.
    const ago = Math.floor(days * (1 - Math.sqrt(next())));
    const day = dayOf(new Date(now.getTime() - (ago + 1) * 86_400_000));
    const [model, device, family, , success] = pick(DEVICES);
    const unsupported = device === null;
    const unit = `${model}-${Math.floor(next() * 400)}`;
    const ok = !unsupported && next() < success;
    const [step, error] = unsupported ? ["A2", "unsupported-device"] : FAILURES[Math.floor(next() * FAILURES.length)];
    const [osFamily, osVersion] = OS[Math.floor(next() * (next() < 0.5 ? 1 : OS.length))];
    const firmware = unsupported ? null : family === "beken" ? (next() < 0.6 ? "openbeken" : "esphome")
      : (next() < 0.55 ? "tasmota" : "esphome");
    const report = {
      ...structuredClone(FIXTURE), id: Math.floor(next() * 2 ** 52).toString(16).padStart(32, "0").slice(-32),
      tag: next() < 0.03 ? "dev" : "user", model, device, family, firmware,
      stock: family === "beken" ? "1.17.1" : next() < 0.7 ? "1.2.0" : "1.1.0",
      result: ok ? "converted" : "failed", stopped: ok ? null : { step, error, text: "" },
      seconds: Math.round(240 + next() * 420), os: { family: osFamily, version: osVersion },
      python: { version: "3.12.4", source: next() < 0.6 ? "portable" : "system" },
    };
    await db.batch(writes(db, report, unit, day));
  }
  for (const [model, asked] of [["NSPANEL", 41], ["MINIR4", 27], ["M5-2C", 12], ["BASICR4", 9]]) {
    db.db.prepare("INSERT INTO wanted_days (day, model, asked) VALUES (?, ?, ?)").run(dayOf(now), model, asked);
  }
  const kept = new Map();
  const STATS = { head: async (key) => (kept.has(key) ? {} : null), put: async (key, text) => kept.set(key, text) };
  await nightly({ DB: db, STATS }, now);
  return { db, snapshot: JSON.parse(kept.get(SNAPSHOT_KEY)) };
}

export async function sampleStats(now = new Date("2026-12-01T03:17:00Z"), reports = 640) {
  return (await sampleDatabase(now, reports)).snapshot;
}

if (import.meta.url === `file://${process.argv[1]}`) {
  process.stdout.write(`${JSON.stringify(await sampleStats(new Date()), null, 2)}\n`);
}
