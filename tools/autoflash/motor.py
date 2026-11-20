"""A device set up to drive a curtain motor - a Sonoff Dual R3 in Sonoff's
motor mode (kinds.Kind.motor_mode) - kept so through a conversion, in
whichever firmware it ends on.

Its two outputs then drive the motor's two windings, one to open and one to
close. Given two independent relays instead, as Tasmota's template and
esphome.yaml give it, a device wired to a motor could have both on at once -
from Home Assistant, its button, its wall switches or a relay's power-on
state - which can burn the motor out. So a device the tool finds in motor
mode ends as a cover: on Tasmota, its shutter on both relays, interlocked,
both off at every start (set_up_tasmota); on ESPHome, the build made for it,
devices/sonoff-dualr3/esphome-motor.yaml, whose outputs are interlocked in
the build itself (routes/espressif/images.image_for). A device in relay mode
is converted as before.

The mode is found where the device keeps it, whatever the route:

- Sonoff's firmware keeps it as deviceMode in its user settings (NVS
  user_usual, content), which the device's Sonoff backup holds: every route
  out of Sonoff's firmware takes the backup before anything else is written
  (docs/espressif-research.md#the-dual-r3s-work-mode). eWeLink's workMode says
  the same (tools/ewelink/relay.motor_mode), but only while the device is on
  Sonoff's firmware and online; the backup is on this computer.
- A device already on Tasmota or ESPHome is asked as the run begins
  (note_running), before it leaves that firmware: Tasmota's interlock or
  shutter, ESPHome's cover.

Either one saying motor is enough: a device whose backup says motor, which an
earlier release converted to two relays, is put right when it next runs the
tool. Only the Espressif route has a kind with a motor mode; no Beken device
the tool converts has one.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any

from tools.autoflash import esphome, kinds, output, records
from tools.autoflash.device import DeviceError
from tools.autoflash.errors import Abort
from tools.espressif import flashmap, inspect_stock_nvs

if TYPE_CHECKING:
    from tools.autoflash.context import Context

# deviceMode, as Sonoff's firmware reads it at start-up: 1 its relays. It
# writes 2, a motor, over none or one outside 1 to 3, and 3 is a mode not
# looked into: every value but 1 is taken for a motor, the safe side.
SONOFF_RELAYS = 1
# What else of the motor Sonoff's firmware keeps there: logged, not carried
# across. It times the travel itself, by the motor's current, and in what
# unit it keeps it is not known; motorDriverReverse swaps the outputs.
SONOFF_MOTOR_KEYS = ("calibrate_time", "motorCalibrate", "calibrateWay", "motorLocation", "limitCurrent",
                     "motorDriverReverse", "motorKeyReverse")

# Tasmota's shutter on relays 1 and 2, interlocked so that one switched on
# turns the other off first, and every relay off at power-on. Its mode 1 is
# one relay to open and one to close; mode 2, which it would take for good
# from relays not interlocked as it starts (Interlock starts it again, once
# shutters are on), is one for power and one for direction, which would run
# both windings to close. So the interlock goes first, and the mode is given.
TASMOTA_COMMANDS = ("Interlock 1,2", "Interlock 1", "SetOption80 1", "ShutterRelay1 1", "ShutterMode 1",
                    "PowerOnState 0")
# Tasmota's commands for the travel times, which it starts at 10 s.
TASMOTA_TRAVEL = "ShutterOpenDuration1 and ShutterCloseDuration1"
# The ESPHome build's, by their names in devices/sonoff-dualr3/esphome-motor.yaml.
ESPHOME_TRAVEL = "Open duration and Close duration"
# How long Tasmota is given to have taken the commands, asked again meanwhile.
TASMOTA_TAKES = 10.0


def sonoff_settings(flash: bytes) -> dict[str, Any] | None:
    """Sonoff's user settings (user_usual, content) from a backup of its
    firmware; None from one that has none."""
    settings = flashmap.nvs(flash)
    names, values = inspect_stock_nvs.parse(flash[settings.offset:settings.offset + settings.size])
    for (namespace, key), value in values.items():
        if names.get(namespace) == "user_usual" and key == "content":
            content = json.loads(value.data.split(b"\x00", 1)[0])
            return content if isinstance(content, dict) else {}
    return None


def sonoff_motor(settings: dict[str, Any] | None) -> bool | None:
    """Whether Sonoff's user settings run the device as a motor; None for no
    settings at all."""
    if settings is None:
        return None
    return str(settings.get("deviceMode")) != str(SONOFF_RELAYS)


def backup_settings(ctx: Context) -> dict[str, Any] | None:
    """Sonoff's user settings in the device's backup on this computer; None
    without a backup, or one whose settings cannot be read."""
    mac = ctx.state.input("device_mac")
    backup = records.find_backup(mac) if mac else None
    if backup is None or backup.family != "espressif":
        return None
    try:
        return sonoff_settings((backup.directory / "flash.bin").read_bytes())
    except (OSError, ValueError, KeyError):
        return None


def tasmota_motor(device: Any) -> bool | None:
    """Whether a Tasmota keeps its relays from both being on, or drives them as
    a shutter: what makes it a motor's, set up by this tool or by hand. None
    for one that does not answer."""
    try:
        interlock = device.command("Interlock")
        shutter = device.command("ShutterRelay1")
    except (DeviceError, OSError):
        return None
    return interlocked(interlock) or (isinstance(shutter, dict) and shutter.get("ShutterRelay1") == 1)


def interlocked(answer: Any) -> bool:
    """Whether Tasmota's Interlock answer has relays 1 and 2 in one group, on."""
    if not isinstance(answer, dict) or str(answer.get("Interlock")).upper() != "ON":
        return False
    return any({"1", "2"} <= set(group.split(",")) for group in str(answer.get("Groups", "")).split())


def tasmota_in_force(device: Any) -> bool:
    """Whether a Tasmota has every setting TASMOTA_COMMANDS gives."""
    try:
        found = {name: device.command(name)
                 for name in ("SetOption80", "Interlock", "ShutterRelay1", "ShutterMode", "PowerOnState")}
    except (DeviceError, OSError):
        return False
    value = lambda name: found[name].get(name) if isinstance(found[name], dict) else None   # noqa: E731
    return (str(value("SetOption80")).upper() == "ON" and interlocked(found["Interlock"])
            and value("ShutterRelay1") == 1 and value("ShutterMode") == 1 and value("PowerOnState") == 0)


def esphome_motor(host: str | None) -> bool | None:
    """Whether an ESPHome drives a cover (the motor's build): None for one
    that does not answer."""
    if not host:
        return None
    if esphome.cover(host) is not None:
        return True
    return False if esphome.is_esphome(host) else None


def running_motor(ctx: Context, firmware: str) -> bool | None:
    """Whether the device, on `firmware`, runs as a motor's: None where it
    does not answer, or the firmware cannot say."""
    if firmware == "tasmota":
        return tasmota_motor(ctx.device)
    if firmware == "esphome":
        return esphome_motor(ctx.state.input("device_ip"))
    return None


def note_running(ctx: Context, firmware: str) -> None:
    """What the firmware the device runs as the run begins says of its mode,
    kept for the run: it is gone once the device leaves that firmware."""
    if kinds.of_run(ctx.state).motor_mode and firmware in ("tasmota", "esphome"):
        found = running_motor(ctx, firmware)
        output.log_only(f"    {firmware} on the device runs it as {'a motor' if found else 'relays'}"
                        + ("" if found is not None else " (it did not say)"))
        if found:
            ctx.state.set_input("motor_running", firmware)


def drives(ctx: Context) -> bool:
    """Whether the run's device drives a motor: its Sonoff backup says so, or
    the firmware it ran as the run began (note_running)."""
    if not kinds.of_run(ctx.state).motor_mode:
        return False
    return bool(ctx.state.input("motor_running")) or bool(sonoff_motor(backup_settings(ctx)))


def where_from(ctx: Context) -> str:
    """Where the run found the device drives a motor, as the log says it."""
    running = ctx.state.input("motor_running")
    return f"the {running} it ran" if running else "its Sonoff backup"


def log_sonoff_motor(ctx: Context) -> None:
    """What the backup holds of Sonoff's own motor settings, for the log: what
    a report of a real one would show of them."""
    settings = backup_settings(ctx) or {}
    kept = {key: settings[key] for key in SONOFF_MOTOR_KEYS if key in settings}
    output.log_only(f"    Motor mode, from {where_from(ctx)}. Sonoff's motor settings in the backup: "
                    f"{json.dumps(kept) if kept else 'none'}")


def tell_set_up(firmware: str, travel: str) -> None:
    """What the person is told of a device set up for its motor: the one thing
    left to do, its travel times."""
    output.tell(f"    Motor mode carried across: {firmware} drives the motor, its two outputs never on together "
                "and both off at every start.")
    output.tell(f"    Time the motor's travel and set {travel}: 10 s until you do.")


def set_up_tasmota(ctx: Context) -> dict[str, Any]:
    """A Tasmota on a device that drives a motor given TASMOTA_COMMANDS, once
    its template is in place, and seen to have them all; nothing for one in
    relay mode. Its travel times are left as they are: Tasmota's own, or the
    person's from before."""
    if not drives(ctx):
        return {}
    log_sonoff_motor(ctx)
    device = ctx.device
    if tasmota_in_force(device):
        output.say("    Tasmota drives the motor already: its outputs interlocked, both off at every start.")
        return {"motor": "in place"}
    device.command("Backlog " + "; ".join(TASMOTA_COMMANDS), timeout=30.0)
    started = time.monotonic()
    while not tasmota_in_force(device):
        if time.monotonic() - started > TASMOTA_TAKES:
            raise Abort("Tasmota did not take the settings that keep the motor's two outputs from both being "
                        "on; it is safe to do this step again")
        time.sleep(1)
    tell_set_up("Tasmota", TASMOTA_TRAVEL)
    return {"motor": "set up"}
