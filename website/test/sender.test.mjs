// api/sender.js: who sent a request, as the API's limits count senders.
import assert from "node:assert/strict";
import { test } from "node:test";
import { CLOUD_ASNS, networkAsn, prefixOf, senderAddress } from "../api/sender.js";

test("an IPv6 sender is its /48, however the address is written", () => {
  const one = prefixOf("2001:db8:1:2:aaaa:bbbb:cccc:dddd");
  assert.equal(one, "2001:0db8:0001::/48");
  for (const same of ["2001:db8:1:2::1", "2001:0db8:0001:0002:ffff::", "2001:DB8:1:2:0:0:0:9"]) {
    assert.equal(prefixOf(same), one, same);
  }
  // A rented server's /48 holds 65,536 /64s: all one sender.
  assert.equal(prefixOf("2001:db8:1:ffff::1"), one, "another /64 in the /48 is the same sender");
  assert.notEqual(prefixOf("2001:db8:2::1"), one, "the next /48 is another sender");
  assert.equal(prefixOf("2001:db8::1"), "2001:0db8:0000::/48");
  assert.equal(prefixOf("fe80::1%eth0"), "fe80:0000:0000::/48");
});

test("an IPv4 sender is its address, and anything unreadable is itself", () => {
  assert.equal(prefixOf("192.0.2.9"), "192.0.2.9");
  assert.equal(prefixOf("unknown"), "unknown");
  assert.equal(prefixOf("zz::1"), "zz::1");
});

test("from Cloudflare's header", () => {
  const request = (address) => new Request("https://api.firmwareliberator.com/readout",
    { headers: address ? { "CF-Connecting-IP": address } : {} });
  assert.equal(senderAddress(request("2001:db8:1:2::77")), "2001:0db8:0001::/48");
  assert.equal(senderAddress(request(null)), "unknown");
});

test("a big cloud's network counts as none, as does a request Cloudflare names none for", () => {
  const on = (asn) => Object.defineProperty(new Request("https://api.firmwareliberator.com/readout"), "cf",
    { value: asn === undefined ? undefined : { asn } });
  assert.equal(networkAsn(on(64500)), 64500);
  assert.ok(CLOUD_ASNS.has(16509));
  assert.equal(networkAsn(on(16509)), null, "Amazon's");
  assert.equal(networkAsn(on(undefined)), null);
  assert.equal(networkAsn(on("64500")), null, "only a number");
  assert.ok(!CLOUD_ASNS.has(13335), "Cloudflare's own, which WARP's users at home come from, still counts");
});
