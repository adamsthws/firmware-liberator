"""The report of a run, for the person to share: what was converted, how long
each stage took, what the device's hardware does, and where to post it.
"""

from __future__ import annotations

import itertools
import os
import platform
import re
import sys
import textwrap
import time
import urllib.parse
from pathlib import Path
from typing import Any, TYPE_CHECKING

from tools.autoflash import __version__, esphome, hostsys, kinds, output, records, report_upload, routes, runs, testers
from tools.autoflash.device import DeviceError
from tools.autoflash.runs import ROOT
from tools.ewelink import relay as ewelink_relay

if TYPE_CHECKING:
    from tools.autoflash.context import Context

# Where a run that stopped is told of: a new issue, on the repository's
# "A conversion went wrong" form (.github/ISSUE_TEMPLATE/bug-report.yml),
# its title and the report's id filled in from the link (issue_url).
ISSUE_URL = "https://github.com/adamsthws/firmware-liberator/issues/new"
ISSUE_FORM = "bug-report.yml"
# Shown once a conversion has succeeded, after the report, in this order.
SPONSORS_URL = "https://github.com/sponsors/adamsthws"
KOFI_URL = "https://ko-fi.com/adamsthws"
DONATE_URL = "https://www.paypal.me/adamsthws"


def device_page_url(device_ip: str | None) -> str | None:
    """The address to open the converted device's Tasmota page at.

    Inside the emulator (tools/espressif/emulator) the device's address only exists in its
    container; the emulator says where your browser reaches it instead.
    """
    emulated = os.environ.get("AUTOFLASH_EMULATOR_URL")
    if emulated:
        return emulated
    return f"http://{device_ip}" if device_ip else None


def computer_description() -> str:
    """The operating system and its version, as someone else would want to know it."""
    if hostsys.IS_WINDOWS:
        release, build = platform.release(), platform.version()
        # Python before 3.12 calls every 10.0 build "10"; Windows 11 is 10.0
        # from build 22000 (the server releases there are named otherwise).
        if (release == "10" and sys.getwindowsversion().product_type == 1
                and int(build.rpartition(".")[2] or 0) >= 22000):
            release = "11"
        system = f"Windows {release} (build {build})"
    elif hostsys.IS_MAC:
        system = f"macOS {platform.mac_ver()[0]}"
    else:
        try:
            system = platform.freedesktop_os_release().get("PRETTY_NAME") or ""
        except OSError:
            system = ""
        system = system or f"Linux {platform.release()}"
    return system


def python_description() -> str:
    """The Python the tool ran on, and where it came from (start.sh, start.cmd)."""
    running = Path(sys.executable).resolve()
    portable = Path(os.environ.get("AUTOFLASH_PYTHON_HOME") or ROOT / ".python").resolve()
    if portable in running.parents:
        source = "portable"
    elif (hostsys.IS_MAC and "Python.framework" in running.parts
          and any((portable / name).exists() for name in ("installed-by-autoflash", "installed-by-s60"))):
        source = "start.sh's"
    elif sys.prefix != sys.base_prefix:
        source = "venv"
    else:
        source = "system"
    return f"{platform.python_version()} ({source})"


def public_text(text: str, limit: int = 240) -> str:
    """`text` fit for a public post: on one line, with nothing that identifies
    the device, the network or the person - not even a private address."""
    text = output.scrub(" ".join(str(text).split()))
    text = re.sub(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w])", "<address>", text)
    return text if len(text) <= limit else text[:limit - 3].rstrip() + "..."


def run_result(ctx: Context, ending: str) -> str:
    """How the run ended, for the report: "converted" ("updated", for an
    update of this tool's firmware), or where and why it stopped.

    ending: "converted", "failed" or "crashed".
    """
    if ending == "converted":
        if ctx.state.input("put_back"):
            return f"put back on {routes.FIRMWARES[runs.run_firmware(ctx.state)]}"
        return "updated" if runs.run_source(ctx.state) == "outdated" else "converted"
    # The stage that failed, not the first left to do: a failure may have sent
    # earlier steps back to be done again (E3 refused as changed sends E1, E2).
    failed = getattr(ctx, "last_failed_step", "")
    stopped = next(((number, step) for number, (step, _, _) in enumerate(routes.STEPS, 1)
                    if (step == failed if failed else not ctx.state.is_done(step))), None)
    # No total before the firmware is chosen: STEPS is then only the start
    # every route shares, and how many stages would follow is not known.
    total = f" of {len(routes.STEPS)}" if routes.ROUTE[0] else ""
    where = (f"stage {stopped[0]}{total} ({routes.stage_name(stopped[1])})"
             if stopped else "the end")
    reason = ctx.last_failure or (
        ctx.state.data.get("steps", {}).get(stopped[1], {}).get("error", "") if stopped else "")
    head = "crashed" if ending == "crashed" else "stopped"
    return f"{head} at {where}" + (f": {public_text(reason)}" if reason else "")


# What a run's starting point is called in its report: one for each of SOURCES.
CONVERTED_FROM = {"sonoff": "Sonoff", "tasmota": "Tasmota", "esphome": "ESPHome", "openbeken": "OpenBeken",
                  "midway": "unfinished run", "committed": "unfinished run",
                  "installer": "unfinished run", "interim": "unfinished run",
                  "safeboot": "Tasmota safeboot",
                  "untemplated": "Tasmota, no template",
                  "outdated": "earlier release"}   # of the run's firmware, named before it


def converted_from(ctx: Context, firmware: str | None, stock_version: str = "") -> str:
    """What the device ran at the run's start, for its report: "__", to fill in,
    for a run that stopped before it found that out (the device not on the
    network at all) - not Sonoff's, which runs.run_source takes when none is
    recorded, for a run of 1.x.  Sonoff's firmware with its version
    (`stock_version`), where the run learned it; another's without, as the
    version a converted device's record keeps is its backup's, not what it ran."""
    if ctx.state.input("replaced_build"):
        return "its own build"
    source = (ctx.state.input("put_back") or {}).get("came_from") or runs.run_source(ctx.state)
    found = (ctx.state.input("source") or ctx.state.input("stock_version")
             or any(map(ctx.state.is_done, ctx.state.data.get("steps", {}))))
    if not found:
        return "__"
    if source == "sonoff":
        return f"{CONVERTED_FROM[source]} {stock_version}".rstrip()
    return (f"{routes.FIRMWARES[firmware]}, " if source == "outdated" and firmware else "") + CONVERTED_FROM[source]


def report_fields(ctx: Context, hardware: dict[str, str] | None = None,
                  result: str = "converted") -> list[tuple[str, str]]:
    """This run's answers for a Test reports post, as (label, value) - nothing private.

    No addresses, Wi-Fi name, account or device id: only what is useful to
    someone deciding whether the tool will work for them.  `hardware` is what
    check_hardware() measured.  "__" marks what the run could not tell.
    """
    hardware = hardware or {}
    kind = kinds.of_run(ctx.state)
    # None for a run stopped before its firmware was chosen (A3).
    firmware = routes.recorded_firmware(ctx.state)
    espressif = kind.family == "espressif"
    # A run that starts on a converted device never asks Sonoff's firmware or
    # eWeLink: what the device's record kept from its first conversion - the
    # model, and the Sonoff firmware its backup holds - stands in.
    record = records.read_device(ctx.state.input("device_mac") or "")
    product = str(ctx.state.input("product_model") or record.get("model") or "")
    # A converted device with no record here says its kind, if not its exact model.
    model = kind.product_of(product) or (kind.name if runs.run_source(ctx.state) != "sonoff" else "")
    stock_version = str(ctx.state.input("stock_version") or record.get("stock_version") or "")
    captures = ctx.state.data.get("captures", {})
    joined = (captures.get("D1.upload_route") == "bridge network"
              or captures.get("D1.resumed") is True
              or (captures.get("D1.upload_route") == "home network" and captures.get("D1.wifi") != "preset")
              or captures.get("F4.wifi_route") == "setup network")
    retries = {number: captures[f"{step}.attempts"] - 1 for number, (step, _, _) in enumerate(routes.STEPS, 1)
               if isinstance(captures.get(f"{step}.attempts"), int) and captures[f"{step}.attempts"] > 1}
    retried = [f"{number} ({count} {'retry' if count == 1 else 'retries'})"
               for number, count in retries.items()]
    # The lowest of the run's readings: from Sonoff's advert at A2, from Tasmota
    # at L1, D4, E1 or I1 - wherever the device's firmware could give one.
    readings = [value for key, value in captures.items() if key.endswith(".signal_dbm")
                and isinstance(value, int) and not isinstance(value, bool)]
    signal = min(readings) if readings else None
    fields = [
        ("Result", result),
        ("Firmware", routes.FIRMWARES[firmware] if firmware else "not chosen"),
        ("From", converted_from(ctx, firmware, stock_version)),
        ("Liberator", __version__),
        ("Model", model or "__"),
        # How it went, never where it is or which device: the path holds the MAC.
        # A run from a converted device takes none: one is there or not.
        ("Backup", records.BACKUP_OUTCOMES[ctx.state.input("backup_outcome")
                                      or ("existing" if routes.sonoff_backup_exists(ctx) else "not taken")][1]),
        # Where going back to Sonoff's firmware took the device's identity from:
        # only on such a run (sonoff.step_sonoff_restore), and in its log, not on
        # the screen (LOG_ONLY).
        *([("Identity", SONOFF_IDENTITY.get(captures.get("R1.identity"), "not read"))]
          if espressif and firmware == "sonoff" else []),
        ("OS", computer_description()),
        ("Python", python_description()),
        # How long it took, each stage's time and how long it kept the device
        # busy are not shown: the report sent carries them (report_data).
        ("Retries", ", ".join(retried) or "none"),
        ("Signal", f"{signal} dBm" if isinstance(signal, int) else "not recorded"),
        # Only where the route has them: the Wi-Fi typed or a setup network
        # joined are an Espressif route's.
        *([("Wi-Fi typed", "yes" if ctx.state.input("wifi_typed") else "no"),
           ("Setup network", "yes" if joined else "no")] if espressif else []),
        # Whether the device had to be switched off and on to start its new
        # firmware: an emulated Beken device never restarts by itself; a real one may.
        *([("Power cycled", "true" if ctx.state.input("power_cycles") else "false")] if not espressif else []),
        ("Checks", checks_passed(hardware)),
        # Not how the meter's calibration went: a device's readings a few
        # percent out is no news to the person converting it. The report
        # sent holds it (report_data.meter_word), the log why.
    ]
    return [(label, answer(value)) for label, value in fields]


SONOFF_IDENTITY = {"backup": "backup", "kept": "device", "account": "eWeLink account"}


def checks_passed(hardware: dict[str, str]) -> str:
    """The end-of-run checks as one answer, whatever the device: for a device,
    its relay switching off and on.  What each check found is in the log."""
    relay = hardware.get("Relay", "not checked")
    if relay in ("not checked", "motor mode"):
        return "not run"
    return "pass" if relay.startswith("switches") else "fail"


def answer(value: str) -> str:
    """A report answer as shown, here and as the run checks it: starting with a
    capital - "No", "Converted".  Only the first letter, so "S60TPG" and
    "6m 32s" stay as they are."""
    return value[:1].upper() + value[1:]


def briefly(result: str) -> str:
    """A result as the screen's report shows it: a stopped run's as "Stopped"
    or "Crashed" alone, not where or why (run_result)."""
    head, at, _ = result.partition(" at ")
    return head if at and head in ("Stopped", "Crashed") else result


def report_markdown(fields: list[tuple[str, str]]) -> str:
    """For pasting into an issue, where the labels show in bold."""
    return "".join(f"**{label}:** {value}\n" for label, value in fields)


# Where each answer goes on the screen: the device's on the right, the result,
# the run's and this computer's on the left.  The answers a fault suite reads
# off the screen are on the right, each the last on its row, but the result,
# read up to the gap after it (tools/espressif/emulator/fault_suite.py).
RIGHT = ("Firmware", "From", "Model", "Backup", "Signal", "Checks")
# Those of RIGHT a fault suite reads, which stay there, last on their rows.
SUITES_READ = ("From", "Backup", "Checks")
# Answers the run's log has, for the fault suite and someone helping, and the
# screen does not: where an identity came from is nothing the person needs.
LOG_ONLY = ("Identity",)
# Columns each side needs at the least, else one column, as Markdown has it.
SIDE = 34


def report_screen(fields: list[tuple[str, str]], columns: int | None = None,
                  right: tuple[str, ...] = RIGHT, below: list[tuple[str, str]] = ()) -> list[str]:
    """For the terminal: plain labels, lined up, in two columns where the
    window is wide enough - half the rows - else one. `right`: the labels
    of the right-hand column, the device's - a conversion's (RIGHT), or a
    readout's (readout.report_fields); the rest on the left. `below`: (label, value) pairs each on
    a row of its own under them, the window's whole width to fit a list in
    before it wraps - between the words a value's no-break spaces do not join.
    Every value on the left starts in one column: those of the left-hand
    column and those below."""
    margin, gap = "    ", "   "
    columns = columns or output.terminal_size((100, 24)).columns
    side = (columns - len(margin) - len(gap) - 2) // 2
    under = list(below)

    def rows_under(width: int) -> list[str]:
        rows = []
        for label, value in under:
            lines = textwrap.wrap(value, max(20, columns - len(margin) - width - 2)) or [""]
            rows.append(f"{margin}{label + ':':<{width}} {lines[0]}".replace("\u00a0", " "))
            rows.extend(f"{margin}{'':<{width}} {line}".replace("\u00a0", " ") for line in lines[1:])
        return rows
    if side < SIDE:
        width = max(len(label) for label, _ in [*fields, *under]) + 1
        return [f"{margin}{label + ':':<{width}} {value}" for label, value in fields] + rows_under(width)

    def cells(column: list[tuple[str, str]], width: int = 0) -> list[str]:
        width = width or max((len(label) for label, _ in column), default=0) + 1
        rows = []
        for label, value in column:
            lines = textwrap.wrap(value, side - width - 1) or [""]
            rows.append(f"{label + ':':<{width}} {lines[0]}")
            rows.extend(" " * (width + 1) + line for line in lines[1:])
        return rows
    left = [(label, value) for label, value in fields if label not in right]
    right = [(label, value) for label, value in fields if label in right]
    # The columns ending together: the device's answers at the bottom that
    # no fault suite reads go left when its column is the longer (an S61's,
    # with fewer of this computer's).
    while len(right) > len(left) + 1 and right[-1][0] not in SUITES_READ:
        left.append(right.pop())
    width = max(len(label) for label, _ in [*left, *under]) + 1
    left, right = cells(left, width), cells(right)
    lines = [(margin + f"{one:<{side}}{gap}{two}").rstrip()
              for one, two in itertools.zip_longest(left, right, fillvalue="")]
    return lines + rows_under(width)


# Long enough for the CSE7766 and Tasmota's energy driver to report a new
# reading. With every relay off Tasmota keeps its last reading until its energy
# watchdog zeroes it, after 4 s without one, on a 1 s tick: 5 s.
METER_SETTLE = 6.0
# How long a device back on Sonoff's firmware may take to reach eWeLink once the
# run has seen it on the network: an Espressif run's last step sees it there,
# seconds before its firmware has dialled eWeLink (seconds). Looked for every 10 s.
ONLINE_WAIT = 180.0
# How long a device on Tasmota, ESPHome or OpenBeken may take to answer again before its
# relay is checked: one that lost power or its Wi-Fi as the run ended is still
# starting, or waiting out a router's restart of up to three minutes and then
# rejoining (seconds).
ANSWER_WAIT = 240.0
MAINS_VOLTS, DEAD_VOLTS = 90.0, 20.0     # clearly live / clearly switched off
LOAD_WATTS, IDLE_WATTS = 1.0, 0.5


def describe_hardware(switched: bool, restored: bool,
                      on: dict[str, Any] | None, off: dict[str, Any] | None) -> dict[str, str]:
    """The report's Relay and Power meter lines, from the relay's
    replies and the meter read with it on and with it off.

    The relay answering both commands proves the firmware drives it; a reading
    that drops when it is off - the voltage, where the meter is on the switched
    side, or the power of whatever is plugged in - proves it really switches.
    """
    def number(reading: dict[str, Any] | None, key: str) -> float | None:
        value = (reading or {}).get(key)
        # Tasmota gives a device with two channels each as a list (the Dual
        # R3's Power): the first is Relay 1's, the one switched.
        if isinstance(value, list) and value:
            value = value[0]
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    def reading(values: dict[str, Any] | None) -> str:
        volts, watts = number(values, "Voltage"), number(values, "Power")
        if volts is None and watts is None:
            return "no reading"
        return ", ".join(part for part in (
            f"{volts:g} V" if volts is not None else "",
            f"{watts:g} W" if watts is not None else "") if part)

    v_on, v_off = number(on, "Voltage"), number(off, "Voltage")
    p_on, p_off = number(on, "Power"), number(off, "Power")
    seen = ((v_on is not None and v_off is not None and v_on >= MAINS_VOLTS and v_off < DEAD_VOLTS)
            or (p_on is not None and p_off is not None and p_on >= LOAD_WATTS and p_off < IDLE_WATTS))
    if not (switched and restored):
        relay = "did not switch"
    elif seen:
        relay = "switches (meter confirms)"
    else:
        relay = "switches (meter unchanged)"
    return {
        "Relay": relay,
        "Power meter": "no readings" if on is None and off is None
                       else f"on: {reading(on)}; off: {reading(off)}",
    }


def check_hardware(ctx: Context) -> dict[str, str]:
    """Switch the relay to the other state and back - two switches - reading the
    meter after each, so one reading is with it on and one with it off.

    Whatever is plugged into the device is switched for a few seconds, without
    asking: the run has just finished, and the device ends as it started. A device
    that lost power or its Wi-Fi as the run ended is waited for first.
    """
    device = ctx.device

    def state(reply: Any) -> str:
        # POWER, or POWER1 on a template with more than one relay.
        return str(reply.get("POWER", reply.get("POWER1", ""))).upper() if isinstance(reply, dict) else ""

    def switch(on: bool) -> bool:
        return state(device.command(f"Power {'ON' if on else 'OFF'}")) == ("ON" if on else "OFF")

    def meter() -> dict[str, Any] | None:
        time.sleep(METER_SETTLE)
        status = device.command("Status 10")
        energy = status.get("StatusSNS", {}).get("ENERGY") if isinstance(status, dict) else None
        return energy if isinstance(energy, dict) else None

    output.say("Checking the relay and power meter...")
    try:
        waited = device.wait_reachable(timeout=ANSWER_WAIT)
        if waited >= 1:
            output.say(f"    The device answered again after {waited:.0f}s.")
        was_on = state(device.command("Power")) == "ON"
        switched = switch(not was_on)
        first = meter()
        restored = switch(was_on)
        second = meter()
    except (DeviceError, OSError, ValueError) as exc:
        output.say(f"    The device did not answer the check: {exc}")
        return said(UNCHECKED)
    on, off = (second, first) if was_on else (first, second)
    return said(describe_hardware(switched, restored, on, off))


# What a check the device did not answer found.
UNCHECKED = {"Relay": "could not be checked", "Power meter": "could not be checked"}


def said(result: dict[str, str]) -> dict[str, str]:
    """`result`, each of its answers shown: every check says them, whatever it
    found, as a fault suite reads them off the screen."""
    for label, value in result.items():
        output.say(f"    {label}: {answer(value)}")
    return result


def check_esphome_hardware(ctx: Context) -> dict[str, str]:
    """check_hardware's two switches and two readings, through ESPHome's web API."""
    host = ctx.state.input("device_ip")
    output.say("Checking the relay and power meter...")
    started = time.monotonic()
    was_on = esphome.relay(host)
    while was_on is None and time.monotonic() - started < ANSWER_WAIT:   # still starting, perhaps
        time.sleep(5)
        was_on = esphome.relay(host)
    if was_on is None:
        output.say(f"    The device did not answer the check within {ANSWER_WAIT:.0f}s.")
        return said(UNCHECKED)
    switched = esphome.switch_relay(host, not was_on)
    time.sleep(METER_SETTLE)
    first = esphome.meter(host) or None
    restored = esphome.switch_relay(host, was_on)
    time.sleep(METER_SETTLE)
    second = esphome.meter(host) or None
    on, off = (second, first) if was_on else (first, second)
    return said(describe_hardware(switched, restored, on, off))


def check_ewelink_hardware(ctx: Context) -> dict[str, str]:
    """check_hardware's two switches and two readings for a device back on
    Sonoff's firmware, through eWeLink as the app switches it and asks for
    its readings.  One that sends none (no meter, or the DualR3, which
    refuses the asking) has its relay checked alone."""
    device_id = str(records.read_device(ctx.state.input("device_mac") or "").get("ewelink_id") or "")
    if not device_id:
        return {"Relay": "not checked", "Power meter": "not checked"}
    output.say("Checking the relay through eWeLink...")
    record = wait_until_online(ctx, device_id)
    if record is not None and ewelink_relay.motor_mode(record.get("params") or {}):
        return said({"Relay": "motor mode", "Power meter": "not checked"})
    if record is not None:
        checked = ewelink_relay.check_relay(
            account=ctx.ewelink, device_id=device_id, say=output.say)
    else:
        output.say(f"    eWeLink has not shown the device online within {ONLINE_WAIT:.0f}s, so its relay "
                   "could not be switched there. Check the eWeLink app.")
        checked = None
    if checked is None:
        return said({"Relay": "could not be checked", "Power meter": "not checked"})
    if checked.on is None and checked.off is None:
        return said({"Relay": "switches (through eWeLink)" if checked.switched and checked.restored
                     else "did not switch", "Power meter": "not checked"})
    return said(describe_hardware(checked.switched, checked.restored, checked.on, checked.off))


def wait_until_online(ctx: Context, device_id: str) -> dict[str, Any] | None:
    """The device's record once eWeLink shows it online, with its relay's state
    in it (or that it drives a motor, with no relay), and it answers there,
    within ONLINE_WAIT: what a Beken run's arrival waits for already, and an
    Espressif run's does not, as its last step sees the device on the network
    only. The state comes with the device's first reports, a second or two
    after it answers (seen in the emulator, where its record starts empty),
    and switching needs it. None by then, and at once when eWeLink cannot be
    reached."""
    started = time.monotonic()
    while True:
        devices = ctx.account_devices()
        record = next((found for found in devices or [] if found.get("deviceid") == device_id and found.get("online")
                       and (ewelink_relay.relay_state(found.get("params") or {}) is not None
                            or ewelink_relay.motor_mode(found.get("params") or {}))), None)
        if record and ewelink_relay.answers(account=ctx.ewelink, device_id=device_id):
            output.progress_clear()
            waited = time.monotonic() - started
            if waited >= 1:
                output.say(f"    eWeLink shows the device online after {waited:.0f}s.")
            return record
        if devices is None or time.monotonic() - started >= ONLINE_WAIT:
            output.progress_clear()
            return None
        output.progress(f"waiting for eWeLink to show the device online... {time.monotonic() - started:.0f}s")
        time.sleep(10)


def invite_report(ctx: Context, seconds: float, resumed: bool) -> None:
    """Ask for a report, with this run's details ready to paste."""
    # OpenBeken takes Tasmota's commands (Power, Status 10): the same check.
    check = {"esphome": check_esphome_hardware, "tasmota": check_hardware, "openbeken": check_hardware,
             "sonoff": check_ewelink_hardware}.get(
        runs.run_firmware(ctx.state), lambda ctx: {})
    from tools.autoflash import report_data   # it reads this module's words
    mac = ctx.state.input("device_mac")
    if mac and records.MAC_RE.match(mac) and report_data.converted_to_another(ctx):
        records.number_conversion(mac)   # whether a report is sent or not, so the count stays true
    with output.working("Generating your report..."):
        hardware = check(ctx)
        fields = report_fields(ctx, hardware, result=run_result(ctx, "converted"))
    report_upload.send(ctx, "converted", seconds, resumed, hardware)
    offer_report(ctx, fields)
    hold_out_hat()


def hold_out_hat() -> None:
    """The links to support the project, under the report of a run that
    finished: a conversion's, or a readout's (readout.end)."""
    output.tell("")
    output.show("You've reached the bit where I hold out my hat!", output.GREEN)
    output.tell("Free software, not free biscuits... Throw your friendly neighbourhood dev a biscuit:")
    output.show_links([(SPONSORS_URL, "GitHub"), (KOFI_URL, "Ko-fi"), (DONATE_URL, "PayPal")], hint=True)


def report_ending(code: int | None, ending: str | None) -> str | None:
    """Which stopped run offers its report: "failed" or "crashed", else None.

    A run that failed offers it as one that converted does (that one already
    has).  Not one the operator stopped - quit at a question (130) or Ctrl-C -
    nor one this computer stopped (the terminal closed: nobody to read it).
    """
    if ending in ("crashed", "cancelled"):
        return "crashed" if ending == "crashed" else None
    return "failed" if code not in (None, 0, 130) else None


def send_cancelled_report(ctx: Context, how: str) -> None:
    """A run the person stopped (`how`: one of report_data.STOPPED_BY) sends
    its report too, and shows none: the stages people stop at say which
    worry them. Nothing is asked of them."""
    ctx.stopped_by = how
    report_upload.send(ctx, "cancelled", time.monotonic() - ctx.run_started - output.waited(), ctx.run_resumed)


def invite_stopped_report(ctx: Context, ending: str) -> None:
    """A run that failed asks for its report too: the runs that fail say most
    about what to fix, and leaving them out would make the tool look better than
    it is.  Not one the operator stopped - that says nothing about the tool.
    ending: "failed" or "crashed"."""
    seconds = time.monotonic() - ctx.run_started - output.waited()
    fields = report_fields(ctx, result=run_result(ctx, ending))
    report_upload.send(ctx, ending, seconds, ctx.run_resumed)
    offer_report(ctx, fields, stopped=run_result(ctx, ending))


def issue_url(stopped: str, report_id: str | None) -> str:
    """A new issue on the "A conversion went wrong" form, its title where the
    run stopped and its report's id filled in: short, so the link fits on a
    line; the report itself is pasted in."""
    title = public_text(stopped, 100)
    query = {"template": ISSUE_FORM, "title": title[:1].upper() + title[1:], **({"report": report_id} if report_id else {})}
    return f"{ISSUE_URL}?{urllib.parse.urlencode(query, quote_via=urllib.parse.quote)}"


def offer_issue(stopped: str) -> None:
    """A link to open an issue for a run that failed with no report to offer
    (cli.stopped_report): `stopped`, as run_result says it, for its title."""
    output.tell("")
    output.tell("If it should not have stopped there, please open an issue:")
    output.show_link(issue_url(stopped, None), "Open an issue")


def offer_report(ctx: Context, fields: list[tuple[str, str]], stopped: str | None = None) -> None:
    """The run's report on the screen. For a run that stopped (`stopped`: how,
    as run_result says it), a link to open an issue for it, with the report
    on the clipboard to paste in: the tool is meant to finish every run, so
    one it could not is something to fix."""
    ctx.reported = True
    output.tell("")
    if stopped:
        copied = hostsys.copy_to_clipboard(report_markdown(fields))
        output.tell("If it should not have stopped there, please open an issue, and paste in "
                    + ("the report, which is on your clipboard:" if copied else "the report below:"))
        output.show_link(issue_url(stopped, getattr(ctx, "last_report_id", None)), "Open an issue")
    output.tell("")
    if stopped:
        # Where and why, said in full when it stopped, and in the issue's
        # report: here one word, so the result fits beside the firmware. In
        # full in the log, for the suites (fault_suite.REPORT_RESULT_FULL).
        output.log_only(f"    Result in full: {answer(stopped)}")
    for label, value in fields:
        if label in LOG_ONLY:
            output.log_only(f"    {label}: {value}")
    for line in report_screen([(label, briefly(value) if label == "Result" else value) for label, value in fields
                               if label not in LOG_ONLY]):
        output.tell(line)
    if stopped and any(value == "__" for _, value in fields):
        output.tell("")
        output.tell("Fill in the blanks (__) if you know them.")
    testers.show_log(kinds.of_run(ctx.state))
