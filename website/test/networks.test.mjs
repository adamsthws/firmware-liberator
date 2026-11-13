// api/networks.js: which networks count toward an agreement.
import assert from "node:assert/strict";
import { test } from "node:test";
import { d1 } from "../api/local-d1.mjs";
import { asnsOf, CLOUD_ASNS, countedNetwork, LIST_SHRINK, NETWORK_LISTS, refreshNetworks } from "../api/networks.js";

const NOW = new Date("2026-11-13T03:17:00Z");
const on = (asn) => Object.defineProperty(new Request("https://api.firmwareliberator.com/v1/readout"), "cf",
  { value: asn === undefined ? undefined : { asn } });
const kept = (bindings) => bindings.DB.db.prepare("SELECT * FROM network_lists ORDER BY name").all()
  .map((row) => ({ ...row, asns: JSON.parse(row.asns) }));
// A fetch answering each list's URL with `texts[name]`: a string, an HTTP
// status, or an Error thrown.
const lists = (texts) => async (url) => {
  const [name] = Object.entries(NETWORK_LISTS).find(([, each]) => each === url);
  const text = texts[name];
  if (text instanceof Error) {
    throw text;
  }
  return typeof text === "number" ? new Response("", { status: text }) : new Response(text);
};

test("a host's, a VPN's or a relay's network counts as none, as does one Cloudflare names none for", async () => {
  const bindings = { DB: d1() };
  assert.equal(await countedNetwork(bindings, on(64500)), 64500);
  for (const [asn, whose] of [[16509, "Amazon"], [24940, "Hetzner"], [9009, "M247, VPNs' exits"],
    [13335, "Cloudflare: WARP and Workers"], [54113, "Fastly: Private Relay"]]) {
    assert.ok(CLOUD_ASNS.has(asn), whose);
    assert.equal(await countedNetwork(bindings, on(asn)), null, whose);
  }
  assert.equal(await countedNetwork(bindings, on(undefined)), null);
  assert.equal(await countedNetwork(bindings, on("64500")), null, "only a number");
});

test("a list is ASNs a line, with comments, and those its build leaves out left out", () => {
  assert.deepEqual(asnsOf("AS64500 # a host, GB\n\n# a comment\nas64502\n-AS64501 # left out\nAS64500\n"),
    [64500, 64502]);
  assert.equal(asnsOf("AS64500\n<html>"), null, "a page, not a list");
  assert.equal(asnsOf("# nothing\n"), null, "none at all");
});

test("the lists fetched each night are kept, and count from then on", async () => {
  const bindings = { DB: d1() };
  assert.equal(await countedNetwork(bindings, on(64500)), 64500);
  assert.deepEqual(await refreshNetworks(bindings, NOW, lists({ datacenter: "AS64500\nAS64501\n", vpn: "AS64510\n" })),
    { datacenter: "taken", vpn: "taken" });
  assert.deepEqual(kept(bindings), [
    { name: "datacenter", asns: [64500, 64501], taken: "2026-11-13", problem: null },
    { name: "vpn", asns: [64510], taken: "2026-11-13", problem: null }]);
  assert.equal(await countedNetwork(bindings, on(64500)), null, "read afresh once the lists are taken");
  assert.equal(await countedNetwork(bindings, on(64510)), null);
  assert.equal(await countedNetwork(bindings, on(64520)), 64520);
});

test("a list that does not come, is not one, or is cut short leaves the one kept, and says why", async () => {
  const bindings = { DB: d1() };
  const many = Array.from({ length: 10 }, (_, at) => `AS${64500 + at}`).join("\n");
  await refreshNetworks(bindings, NOW, lists({ datacenter: many, vpn: "AS64510" }));
  const later = new Date("2026-11-14T03:17:00Z");
  assert.ok(LIST_SHRINK * 10 > 4);
  assert.deepEqual(await refreshNetworks(bindings, later, lists({ datacenter: "AS64500\nAS64501\nAS64502\nAS64503",
    vpn: 404 })), { datacenter: "4 ASNs, where 10 were kept", vpn: "HTTP 404" });
  assert.deepEqual(kept(bindings).map((row) => [row.name, row.asns.length, row.taken]),
    [["datacenter", 10, "2026-11-13"], ["vpn", 1, "2026-11-13"]], "each as it was");
  assert.deepEqual(await refreshNetworks(bindings, later, lists({ datacenter: "<html>", vpn: new Error("timed out") })),
    { datacenter: "not a list of ASNs", vpn: "timed out" });
  assert.deepEqual(await refreshNetworks(bindings, later, lists({ datacenter: "AS64500\n".repeat(200_000), vpn: "AS64511" })),
    { datacenter: "not a list of ASNs", vpn: "taken" }, "nor one past the size of any");
});

test("a list never taken is kept as none, from the day it was first tried", async () => {
  const bindings = { DB: d1() };
  await refreshNetworks(bindings, NOW, lists({ datacenter: 500, vpn: 500 }));
  await refreshNetworks(bindings, new Date("2026-11-20T03:17:00Z"), lists({ datacenter: 500, vpn: 500 }));
  assert.deepEqual(kept(bindings)[0], { name: "datacenter", asns: [], taken: "2026-11-13", problem: "HTTP 500" });
});
