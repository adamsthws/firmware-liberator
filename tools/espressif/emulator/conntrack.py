#!/usr/bin/env python3
"""The connections an emulator's firewall tracks, read over netlink and printed
as /proc/net/nf_conntrack prints them: for a kernel built without that file
(CONFIG_NF_CONNTRACK_PROCFS unset, as Ubuntu's are), where the fault suite
would otherwise see none of its device's connections. The suite pipes this file
to the emulator's own Python (docker exec ... python3 -), which reads its own
network namespace's table: so it imports nothing but the standard library.

Only TCP and UDP over IPv4, the protocol, state, source and destination and
ports of each, which is all the suite reads (fault_suite.established,
connection_changes, device_fetching).
"""
from __future__ import annotations

import os
import socket
import struct
import sys

NETLINK_NETFILTER = 12
# nfnetlink's conntrack subsystem (NFNL_SUBSYS_CTNETLINK): asked with "get", each
# entry of a dump answered as "new" (IPCTNL_MSG_CT_GET, IPCTNL_MSG_CT_NEW).
CT_GET, CT_NEW = (1 << 8) | 1, (1 << 8) | 0
NLM_F_REQUEST, NLM_F_DUMP = 0x1, 0x300
NLMSG_ERROR, NLMSG_DONE = 2, 3
# linux/netfilter/nfnetlink_conntrack.h
CTA_TUPLE_ORIG, CTA_PROTOINFO, CTA_TIMEOUT = 1, 4, 7
CTA_TUPLE_IP, CTA_TUPLE_PROTO = 1, 2
CTA_IP_V4_SRC, CTA_IP_V4_DST = 1, 2
CTA_PROTO_NUM, CTA_PROTO_SRC_PORT, CTA_PROTO_DST_PORT = 1, 2, 3
CTA_PROTOINFO_TCP, CTA_PROTOINFO_TCP_STATE = 1, 1
TYPE_MASK = 0x3FFF  # without NLA_F_NESTED and NLA_F_NET_BYTEORDER
# linux/netfilter/nf_conntrack_tcp.h, as /proc/net/nf_conntrack names them.
TCP_STATES = ("NONE", "SYN_SENT", "SYN_RECV", "ESTABLISHED", "FIN_WAIT", "CLOSE_WAIT",
              "LAST_ACK", "TIME_WAIT", "CLOSE", "SYN_SENT2")
PROTOCOLS = {6: "tcp", 17: "udp"}


def attributes(data: bytes) -> dict[int, bytes]:
    """A netlink message's attributes, by type: the first of each."""
    found, at = {}, 0
    while at + 4 <= len(data):
        length, kind = struct.unpack_from("=HH", data, at)
        if length < 4:
            break
        found.setdefault(kind & TYPE_MASK, data[at + 4:at + length])
        at += (length + 3) & ~3
    return found


def line(payload: bytes) -> str | None:
    """One conntrack entry (the payload after nfgenmsg) as nf_conntrack's line,
    or None for one this does not print."""
    top = attributes(payload)
    orig = attributes(top.get(CTA_TUPLE_ORIG, b""))
    ip, proto = attributes(orig.get(CTA_TUPLE_IP, b"")), attributes(orig.get(CTA_TUPLE_PROTO, b""))
    number = proto.get(CTA_PROTO_NUM, b"\0")[0]
    if number not in PROTOCOLS or CTA_IP_V4_SRC not in ip:
        return None
    timeout = struct.unpack(">I", top[CTA_TIMEOUT])[0] if CTA_TIMEOUT in top else 0
    state = ""
    if number == 6:
        tcp = attributes(attributes(top.get(CTA_PROTOINFO, b"")).get(CTA_PROTOINFO_TCP, b""))
        code = tcp.get(CTA_PROTOINFO_TCP_STATE, b"\0")[0]
        state = (TCP_STATES[code] if code < len(TCP_STATES) else "NONE") + " "
    sport = struct.unpack(">H", proto.get(CTA_PROTO_SRC_PORT, b"\0\0"))[0]
    dport = struct.unpack(">H", proto.get(CTA_PROTO_DST_PORT, b"\0\0"))[0]
    return (f"ipv4     2 {PROTOCOLS[number]:<8} {number} {timeout} {state}"
            f"src={socket.inet_ntoa(ip[CTA_IP_V4_SRC])} dst={socket.inet_ntoa(ip[CTA_IP_V4_DST])} "
            f"sport={sport} dport={dport}")


def lines(messages: bytes) -> tuple[list[str], bool]:
    """The lines in a batch of netlink messages, and whether the dump ended in it."""
    found, at = [], 0
    while at + 16 <= len(messages):
        length, kind = struct.unpack_from("=IH", messages, at)
        if length < 16:
            break
        if kind == NLMSG_DONE:
            return found, True
        if kind == NLMSG_ERROR:
            code = struct.unpack_from("=i", messages, at + 16)[0]
            if code:
                raise OSError(-code, os.strerror(-code))
        elif kind == CT_NEW:
            entry = line(messages[at + 20:at + length])  # after nlmsghdr (16) and nfgenmsg (4)
            if entry:
                found.append(entry)
        at += (length + 3) & ~3
    return found, False


def dump() -> list[str]:
    """Every IPv4 entry of this network namespace's conntrack table."""
    with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, NETLINK_NETFILTER) as sock:
        sock.bind((0, 0))
        request = struct.pack("=IHHII", 20, CT_GET, NLM_F_REQUEST | NLM_F_DUMP, 1, 0)
        sock.send(request + struct.pack("=BBH", socket.AF_INET, 0, 0))
        found = []
        while True:
            more, done = lines(sock.recv(1 << 16))
            found += more
            if done:
                return found


if __name__ == "__main__":
    try:
        print("\n".join(dump()))
    except OSError as exc:
        sys.exit(f"conntrack: {exc}")
