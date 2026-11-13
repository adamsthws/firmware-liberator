// Who sent a request, as the API's limits count senders: an IPv4 address
// whole, and an IPv6 one by its first 48 bits (its /48). One home is given a
// /64 at least, often a /56 or a /48, and one rented server as easily a /48:
// 65,536 /64s, each of which would otherwise seem another sender. A few homes
// on one provider may share a /48 and be counted as one, which the limits
// allow for. One with many IPv4 addresses still seems many. Never stored as
// it is: index.js and reports.js keep only a keyed hash of it.

// Networks anyone can rent servers on by the minute, by ASN (Amazon, Google,
// Microsoft, DigitalOcean, Hetzner, OVH, Akamai's Linode, Vultr, Oracle,
// Contabo, Alibaba): a request from one counts toward no agreement of networks
// (index.js's MIN_NETWORKS, nightly.js's VOUCH_NETWORKS), so three made-up
// readouts or names from three clouds are not enough. It is still taken, as
// from a network Cloudflare does not name: an owner behind a VPN, or at work
// for one of these, loses nothing but that vote. Kept to the big clouds;
// consumer VPNs and relays (Cloudflare's WARP, iCloud Private Relay) are left
// out, as many owners use them at home. One gone stale only lets that cloud
// vote again.
export const CLOUD_ASNS = new Set([16509, 14618, 15169, 396982, 8075, 14061, 24940, 16276, 63949, 20473, 31898,
  51167, 45102]);

// The network a request came from (Cloudflare's ASN), as agreements count
// networks: null where Cloudflare gives none, or it is a cloud's.
export function networkAsn(request) {
  const asn = request.cf?.asn;
  return Number.isInteger(asn) && !CLOUD_ASNS.has(asn) ? asn : null;
}

// The sender's address from Cloudflare's header, IPv6 cut to its /48.
export function senderAddress(request) {
  return prefixOf(request.headers.get("CF-Connecting-IP") || "unknown");
}

// An address as senders are counted: an IPv6 one's first three groups,
// written out in full (so 2001:db8::1 and 2001:0db8:0:5::2 are one sender);
// anything else as it is.
export function prefixOf(address) {
  const text = String(address).trim().toLowerCase();
  if (!text.includes(":")) {
    return text;
  }
  const [head, tail = ""] = text.split("%")[0].split("::");
  const groups = (part) => (part ? part.split(":") : []);
  let words = groups(head);
  if (text.includes("::")) {
    const after = groups(tail);
    words = [...words, ...Array(Math.max(0, 8 - words.length - after.length)).fill("0"), ...after];
  }
  if (words.length < 3 || words.some((word) => !/^[0-9a-f]{1,4}$/.test(word))) {
    return text;   // not one we can read: counted as it is
  }
  return `${words.slice(0, 3).map((word) => word.padStart(4, "0")).join(":")}::/48`;
}
