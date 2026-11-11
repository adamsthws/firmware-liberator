"""A run's report as data, for the project's API to count: what the screen's
report says (report.py), as fixed words and bounded numbers, and nothing that
identifies the person, their computer or their network. Read from the run's
record, not from the screen's words, which change as the screen does.

The device is named by a hash of its MAC, the same from any computer, so a
second run on it is known as the same device; the API stores only a keyed
hash of that. Every value here is one tools/repo/report_schema.py allows,
which the API holds every report to.
"""

from __future__ import annotations

import hashlib
import os
import re
from typing import Any, TYPE_CHECKING

from tools.autoflash import __version__, failures, hostsys, kinds, records, routes, runs
from tools.autoflash import report

if TYPE_CHECKING:
    from tools.autoflash.context import Context

FORMAT = 1
DEVICE_PREFIX = "firmware-liberator device:"
# "put-back": the run ended with the device on the firmware it came from
# (report.run_result's "put back on ..."); "cancelled": the person stopped it.
RESULTS = ("converted", "updated", "put-back", "failed", "crashed", "cancelled")
# How the person stopped a cancelled run, sent as its stopped "error": q at a
# question, Ctrl-C (at once, or confirmed at a stage's end), or its window closed.
STOPPED_BY = ("quit", "ctrl-c", "window-closed")
OS_FAMILIES = ("windows", "macos", "linux")
# Where the Python came from, by report.python_description's word for it;
# "other" for one it has since learnt.
PYTHON_SOURCES = {"portable": "portable", "start.sh's": "start-sh", "venv": "venv", "system": "system"}
PYTHON_WORDS = (*PYTHON_SOURCES.values(), "other")
# The end-of-run relay check (report.check_hardware and its kin), by how
# their Relay answer starts: anything else was not run or could not be.
CHECKS = ("passed", "failed", "not-run")
# How the run left the meter's calibration, from what its steps found
# (report.meter_calibration reads the same): Sonoff's own, Tasmota carrying
# Sonoff's over (with its factory figures or not), skipped, ESPHome's
# Calibration sensor's source, or not checked.
METER_WORDS = ("sonoff", "carried", "carried-factory", "skipped", "stock-nvs", "from-backup", "missing",
               "reported", "not-checked")
TEXT_LIMIT = 200
MODEL = re.compile(r"[A-Z0-9][A-Z0-9-]{0,23}")
VERSION = re.compile(r"\d{1,4}(\.\d{1,4}){1,3}")
OS_VERSION = re.compile(r"[A-Za-z0-9 ._()+/-]{1,48}")


def device_hash(mac: str | None) -> str | None:
    """The device, as the same 64 hex digits from any computer: None without a MAC."""
    digits = re.sub(r"[^0-9a-f]", "", (mac or "").lower())
    if len(digits) != 12:
        return None
    normal = ":".join(digits[i:i + 2] for i in range(0, 12, 2))
    return hashlib.sha256(f"{DEVICE_PREFIX}{normal}".encode()).hexdigest()


def os_info() -> dict[str, str]:
    """The operating system's family, and its version as report.py words it."""
    family = "windows" if hostsys.IS_WINDOWS else "macos" if hostsys.IS_MAC else "linux"
    words = re.sub(r"^(Windows|macOS|Linux) ", "", report.computer_description()).strip()
    words = "".join(char for char in words if OS_VERSION.fullmatch(char))[:48].strip()
    return {"family": family, "version": words or "unknown"}


def python_info() -> dict[str, str]:
    version, _, source = report.python_description().partition(" (")
    return {"version": version if VERSION.fullmatch(version) else "unknown",
            "source": PYTHON_SOURCES.get(source.rstrip(")"), "other")}


def meter_word(captures: dict[str, Any], firmware: str | None) -> str:
    """How the run left the meter's calibration: one of METER_WORDS."""
    if firmware == "sonoff":
        return "sonoff"
    found: Any = None
    for key, value in captures.items():
        if key.endswith(".meter") or key.endswith(".calibration"):
            found = value   # the last step's counts: an update checks it again
    if isinstance(found, dict) and "skipped" in found:
        return "skipped"
    if isinstance(found, dict) and "changed" in found:
        return "carried-factory" if found.get("factory") else "carried"
    said = found.get("calibration") if isinstance(found, dict) else found
    if isinstance(said, str) and said:
        return next((word for start, word in (("stock NVS", "stock-nvs"), ("from backup", "from-backup"),
                                              ("missing", "missing")) if said.startswith(start)), "reported")
    return "not-checked"


def checks_word(hardware: dict[str, str] | None) -> str:
    relay = (hardware or {}).get("Relay", "")
    return "passed" if relay.startswith("switches") else "failed" if relay == "did not switch" else "not-run"


def plain(text: str, limit: int = TEXT_LIMIT) -> str:
    """`text` for the API: report.public_text's, in printable ASCII alone."""
    text = "".join(char for char in report.public_text(text, limit) if " " <= char <= "~")
    return text[:limit]


def stopped_step(ctx: Context) -> str | None:
    """The step a run stopped at, as report.run_result finds it."""
    failed = getattr(ctx, "last_failed_step", "")
    if failed:
        return failed
    return next((step for step, _, _ in routes.STEPS if not ctx.state.is_done(step)), None)


def stages(ctx: Context) -> dict[str, dict[str, int]]:
    """Each step the run timed: its seconds, how long it kept the device busy,
    and how many times it was tried."""
    captures = ctx.state.data.get("captures", {})
    found = {}
    for step, _, _ in routes.STEPS:
        seconds = captures.get(f"{step}.seconds")
        if not isinstance(seconds, (int, float)) or isinstance(seconds, bool):
            continue
        busy = captures.get(f"{step}.device_busy_seconds")
        tries = captures.get(f"{step}.attempts")
        found[step] = {"s": min(int(seconds), 86400),
                       "busy": min(int(busy), 86400) if isinstance(busy, (int, float)) else 0,
                       "tries": min(tries, 99) if isinstance(tries, int) and tries > 0 else 1}
    return found


def payload(ctx: Context, report_id: str, seconds: float, resumed: bool, ending: str,
            hardware: dict[str, str] | None = None) -> dict[str, Any]:
    """The run's report as the API takes it, but its proof. `ending`:
    "converted", "failed", "crashed" or "cancelled" (how: ctx.stopped_by,
    one of STOPPED_BY)."""
    state = ctx.state
    kind = kinds.by_key(state.input("device_kind"))
    firmware = routes.recorded_firmware(state)
    espressif = kinds.of_run(state).family == "espressif"
    record = records.read_device(state.input("device_mac") or "")
    captures = state.data.get("captures", {})
    source = runs.run_source(state)
    product = str(state.input("product_model") or record.get("model") or "").strip().upper()
    stock = str(state.input("stock_version") or record.get("stock_version") or "")
    readings = [value for key, value in captures.items() if key.endswith(".signal_dbm")
                and isinstance(value, int) and not isinstance(value, bool)]
    put_back = state.input("put_back") or {}
    if ending == "converted":
        result = "put-back" if put_back else "updated" if source == "outdated" else "converted"
        stopped = None
    elif ending == "cancelled":
        # Where people stop, and how: no words, as nothing went wrong.
        result = ending
        stopped = {"step": stopped_step(ctx), "error": getattr(ctx, "stopped_by", None) or "quit", "text": ""}
    else:
        reason = ctx.last_failure or ""
        text = plain(reason)
        result = ending
        # Classed by the words sent, as the API checks them (website/api/review.js),
        # but for words cut short, where the whole reason says more.
        stopped = {"step": stopped_step(ctx),
                   "error": "crash" if ending == "crashed"
                   else failures.classify(reason if text.endswith("...") else text),
                   "text": text}
    joined = (captures.get("D1.upload_route") == "bridge network" or captures.get("D1.resumed") is True
              or (captures.get("D1.upload_route") == "home network" and captures.get("D1.wifi") != "preset")
              or captures.get("F4.wifi_route") == "setup network")
    backup = state.input("backup_outcome") or ("existing" if routes.sonoff_backup_exists(ctx) else "not taken")
    return {
        "format": FORMAT,
        "id": report_id,
        "tag": "dev" if os.environ.get("AUTOFLASH_REPORT_TAG") == "dev" else "user",
        "tool": __version__,
        "kind": "run",
        "result": result,
        "stopped": stopped,
        "family": kind.family if kind else None,
        "device": kind.key if kind else None,
        "model": product if MODEL.fullmatch(product) else None,
        "stock": stock if VERSION.fullmatch(stock) else None,
        "device_hash": device_hash(state.input("device_mac")),
        "firmware": firmware,
        "from": put_back.get("came_from") or source,
        "backup": backup,
        "identity": captures.get("R1.identity") if captures.get("R1.identity") in ("backup", "kept", "account")
        else None,
        "os": os_info(),
        "python": python_info(),
        "seconds": max(0, min(int(seconds), 7 * 86400)),
        "resumed": bool(resumed),
        "stages": stages(ctx),
        "signal": max(-127, min(min(readings), 0)) if readings else None,
        "wifi_typed": bool(state.input("wifi_typed")) if espressif else None,
        "setup_network": joined if espressif else None,
        "power_cycled": bool(state.input("power_cycles")) if not espressif else None,
        "checks": checks_word(hardware),
        "meter": meter_word(captures, firmware) if kinds.of_run(state).has_meter else None,
    }
