"""Whether a device's eWeLink identity could be rebuilt from its eWeLink
account record, worked out on the computer that holds both, and said as
outcomes alone: for each field of a fixed set (ESF_FIELDS, AIS_FIELDS), one of
OUTCOMES - never a value, a hash of one or its length.

A readout (tools/autoflash/readout_espressif.py) runs it on the flash it read,
before the copy is anonymised, and a conversion (routes/espressif/installer.py,
C3) on the backup it has just taken; the outcomes go in the readout's manifest
and in its or the run's report (docs/what-the-tool-sends.md).

- **Sonoff's esf SDK** (the S60's and the classic ESP32 devices' firmware):
  each field sonoff_identity.from_account rebuilds, compared with the device's
  own frame_params and data_backups; whether the device has each entry the
  rebuild leaves out (productId, channel_params, PID); and whether the rebuild
  matches in every field it holds.
- **Sonoff's ais SDK** (the BASICR4's and MINIR4's, kinds.Kind.ais_sdk): its
  factory record (tools/espressif/ais_records.py) against the account record
  and the chip's own MAC, whether its SHA-256 is worked out again from its
  fields, and whether a record rebuilt from the account and the chip's MAC is
  the device's byte for byte.

docs/espressif-research.md, "A device's identity and stores" and "Sonoff's ais
SDK", says what the outcomes settle.
"""

from __future__ import annotations

import json
import struct
from typing import Any

from tools.espressif import ais_records, flashmap, sonoff_identity
from tools.espressif.inspect_stock_nvs import parse

# Each field's outcome: the device's value is the rebuilt one; is not (or the
# device has one the rebuild leaves out); the device has none; or it cannot be
# told (the account record or the device's entry is not whole).
OUTCOMES = ("yes", "no", "missing", "unknown")
YES, NO, MISSING, UNKNOWN = OUTCOMES
# The firmware's SDK, by where its identity is; "neither" for a flash holding
# neither frame_params nor a factory record.
PLATFORMS = ("esf", "ais", "neither")

# The entries sonoff_identity.from_account rebuilds, and the fields of each it fills.
REBUILT = {"frame_params": ("init", "uiid", "deviceid", "apikey", "model", "dis_server", "dis_port",
                            "encrypt_key", "is_encrypt"),
           "data_backups": ("init", "deviceid", "apikey", "model")}
# The entries of the identity (sonoff_identity.KEPT) the rebuild leaves out:
# whether the device has each.
LEFT_OUT = ("productId", "channel_params", "PID")
WHOLE = "rebuild_whole_matches"
ESF_FIELDS = (*(f"{entry}.{name}" for entry, names in REBUILT.items() for name in names),
              *(f"{entry}.present" for entry in LEFT_OUT), WHOLE)
AIS_FIELDS = ("record_version_known", "deviceid_matches", "apikey_matches_devicekey", "model_matches",
              "uiid_matches", "checksum_recomputes", "chip_mac_is_efuse_mac", "sta_mac_is_efuse_mac",
              "sap_mac_is_efuse_mac_plus_one", WHOLE)
FIELDS = {"esf": ESF_FIELDS, "ais": AIS_FIELDS, "neither": ()}
# Every field's name, for the report's schema (tools/repo/report_schema.py).
ALL_FIELDS = tuple(dict.fromkeys((*ESF_FIELDS, *AIS_FIELDS)))

FACTORY = ("factory", "info")


def _settings(flash: bytes) -> dict[tuple[str, str], bytes]:
    """Each (namespace, key) the NVS partitions hold, and its bytes: the
    settings' own partition ("nvs") first, so its entry is the one kept."""
    try:
        spans = sorted(flashmap.nvs_partitions(flash), key=lambda found: found.label != flashmap.NVS_LABEL)
        places = [(found.offset, found.size) for found in spans]
    except ValueError:
        places = []
    found: dict[tuple[str, str], bytes] = {}
    for start, size in places or [(0x9000, 0x10000)]:
        try:
            names, values = parse(flash[start:start + size])
        except (ValueError, struct.error):
            continue
        for (space, key), value in values.items():
            if value.kind in (0x21, 0x41, 0x48):
                data = value.data[:value.size] if value.kind == 0x48 else value.data
                found.setdefault((names.get(space, ""), key), data.split(b"\0", 1)[0] if value.kind == 0x21 else data)
    return found


def _json(data: bytes | None) -> dict[str, Any] | None:
    if data is None:
        return None
    try:
        value = json.loads(data.split(b"\0", 1)[0])
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def _compare(has: bool, value: Any, rebuilt: dict[str, Any] | None, name: str) -> str:
    if rebuilt is None:
        return UNKNOWN
    if not has:
        return MISSING
    return YES if name in rebuilt and rebuilt[name] == value else NO


def _esf(settings: dict[tuple[str, str], bytes], device: dict[str, Any], dispatch: tuple[str, int],
         encrypted: bool) -> dict[str, str]:
    try:
        identity = sonoff_identity.from_account(device, dispatch, encrypted=encrypted)
        rebuilt: dict[str, dict[str, Any]] | None = {item.namespace: json.loads(item.data)
                                                     for item in identity.items}
    except sonoff_identity.IdentityError:
        rebuilt = None
    outcomes: dict[str, str] = {}
    unreadable = False
    # Every field the rebuild writes the same on the device. One it leaves
    # out (the S60's encrypt_key) is told in its own outcome but not counted:
    # the firmware goes online without it, rebuilt as from_account builds it.
    alike = True
    for entry, names in REBUILT.items():
        own = _json(settings.get((entry, "content")))
        mine = None if rebuilt is None else rebuilt.get(entry, {})
        # There, but not Sonoff's JSON: nothing in it can be told.
        broken = own is None and (entry, "content") in settings
        unreadable = unreadable or broken
        for name in names:
            outcomes[f"{entry}.{name}"] = (UNKNOWN if broken else
                                           _compare(own is not None and name in own, (own or {}).get(name), mine, name))
            if mine is None or name in mine:
                alike = alike and mine is not None and (own or {}).get(name) == mine.get(name)
    for entry in LEFT_OUT:
        outcomes[f"{entry}.present"] = YES if any(where[0] == entry for where in settings) else NO
    outcomes[WHOLE] = UNKNOWN if rebuilt is None or unreadable else YES if alike else NO
    return outcomes


def _mac_text(raw: bytes, colons: bool) -> str:
    return (":" if colons else "").join(f"{byte:02X}" for byte in raw)


def _hex(value: bytes) -> str:
    return value.decode("ascii", "replace").replace(":", "").upper()


def _ais(record: bytes, device: dict[str, Any], efuse: bytes | None) -> dict[str, str]:
    outcomes = dict.fromkeys(AIS_FIELDS, UNKNOWN)
    if not ais_records.is_factory(record):
        outcomes["record_version_known"] = NO
        return outcomes
    outcomes["record_version_known"] = YES
    field = lambda name: ais_records.text(record, ais_records.FACTORY_FIELDS, name)  # noqa: E731
    uiid = struct.unpack_from("<H", record, ais_records.FACTORY_UIID)[0]
    extra = device.get("extra") or {}

    def matches(own: Any, theirs: Any) -> str:
        if own in (b"", None):
            return MISSING
        return UNKNOWN if not theirs else YES if own == theirs else NO

    outcomes["deviceid_matches"] = matches(field("deviceid"), str(device.get("deviceid") or "").encode())
    outcomes["apikey_matches_devicekey"] = matches(field("factory_apikey"), str(device.get("devicekey") or "").encode())
    outcomes["model_matches"] = matches(field("device_model"), str(extra.get("model") or "").encode())
    try:
        theirs = int(extra["uiid"])
    except (KeyError, TypeError, ValueError):
        theirs = None
    outcomes["uiid_matches"] = matches(uiid or None, theirs)
    outcomes["checksum_recomputes"] = (YES if record[ais_records.FACTORY_DIGEST:] == ais_records.factory_digest(record)
                                       else NO)
    try:
        base = bytes.fromhex(flashmap.mac_of_efuse(efuse).replace(":", "")) if efuse else b""
    except (ValueError, IndexError):
        base = b""
    if len(base) != 6:
        return outcomes
    after = (int.from_bytes(base, "big") + 1).to_bytes(6, "big")
    for name, mac in (("chip_mac_is_efuse_mac", base), ("sta_mac_is_efuse_mac", base),
                      ("sap_mac_is_efuse_mac_plus_one", after)):
        own = field(name.split("_is_")[0])
        outcomes[name] = MISSING if not own else YES if _hex(own) == mac.hex().upper() else NO
    # Rebuilt from the account and the chip: the MACs as the documented layout
    # writes the chip's, colons in the other two, upper-case as the chip's.
    try:
        if not (device.get("deviceid") and device.get("devicekey") and extra.get("model")):
            raise KeyError("the account record lacks the device's id, key or model")
        values = {"chip_mac": _mac_text(base, False), "sta_mac": _mac_text(base, True),
                  "sap_mac": _mac_text(after, True), "device_model": str(extra["model"]),
                  "deviceid": str(device["deviceid"]), "factory_apikey": str(device["devicekey"])}
        outcomes[WHOLE] = YES if ais_records.factory_record(values, int(extra["uiid"])) == record else NO
    except (KeyError, TypeError, ValueError):
        pass   # the account record lacks a field, or one too long: unknown
    return outcomes


def check(flash: bytes, device: dict[str, Any], *, efuse: bytes | None, dispatch: tuple[str, int],
          encrypted: bool) -> dict[str, Any]:
    """The outcomes for this device's flash against its eWeLink account record
    `device`: {"platform": one of PLATFORMS, "fields": {name: outcome}}.
    `efuse`, the chip's eFuses (its MAC), None where not read; `dispatch` and
    `encrypted`, as sonoff_identity.from_account takes them."""
    settings = _settings(flash)
    if FACTORY in settings:
        return {"platform": "ais", "fields": _ais(settings[FACTORY], device, efuse)}
    if ("frame_params", "content") in settings:
        return {"platform": "esf", "fields": _esf(settings, device, dispatch, encrypted)}
    return {"platform": "neither", "fields": {}}


def valid(found: Any) -> dict[str, Any] | None:
    """`found` if it is outcomes check() gives, else None: what a report
    sends, from state a run kept."""
    if not isinstance(found, dict) or set(found) != {"platform", "fields"} or found["platform"] not in PLATFORMS:
        return None
    fields = found["fields"]
    if not isinstance(fields, dict) or not all(name in FIELDS[found["platform"]] and outcome in OUTCOMES
                                               for name, outcome in fields.items()):
        return None
    return {"platform": found["platform"], "fields": dict(fields)}


def describe(found: dict[str, Any]) -> str:
    """The outcomes in a line, for the run's log: nothing but them."""
    fields = found["fields"]
    matched = sum(outcome == YES for outcome in fields.values())
    return (f"Identity check ({found['platform']}): {matched} of {len(fields)} yes; "
            + ", ".join(f"{name} {outcome}" for name, outcome in fields.items()))
