"""A device set up to drive a motor - a Sonoff Dual R3 in Sonoff's motor mode
(kinds.Kind.motor_mode), on a curtain, blind, awning or gate - kept so
through a conversion, in whichever firmware it ends on, with its travel
times and the way round its outputs drive it.

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

What the motor is set to is carried across the same way (motor_setup): the
firmware the device ran as the run begins, which has the person's latest,
else Sonoff's own from the backup - its calibrated travel time and whether
its outputs are reversed (docs/espressif-research.md#the-dual-r3s-work-mode)
- else DEFAULT_TRAVEL, the right way round.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
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
# What else of the motor Sonoff's firmware keeps there, all logged. Carried
# across: calibrate_time, the travel its calibration timed by the motor's
# current, in milliseconds, one time for both ways, once motorCalibrate says
# it is calibrated; and motorDriverReverse, its outputs swapped - output 2
# opens and output 1 closes (eWeLink's outputReverse). Not carried:
# motorKeyReverse and motorSwMode, which change how its wall switches work in
# a way not seen (the emulator cannot switch them), and the rest, which only
# its own timing uses.
SONOFF_MOTOR_KEYS = ("calibrate_time", "motorCalibrate", "calibrateWay", "motorLocation", "limitCurrent",
                     "motorDriverReverse", "motorKeyReverse", "motorSwMode")
SONOFF_TRAVEL_UNIT = 0.001   # calibrate_time's, in seconds

# The travel time, each way, either firmware is given where nothing better is
# known (seconds), and the person told to time theirs. Short is the safe side:
# the motor stops early, where a long one could drive a motor with no limit
# switches past its end. devices/sonoff-dualr3/esphome-motor.yaml starts at it.
DEFAULT_TRAVEL = 3.0
# The travel times taken as a real motor's (seconds): ESPHome's settings'
# limits, in esphome-motor.yaml. Outside them, DEFAULT_TRAVEL.
TRAVEL_RANGE = (1.0, 600.0)

# Tasmota's shutter on relays 1 and 2, interlocked so that one switched on
# turns the other off first, and every relay off at power-on. Its mode 1 is
# one relay to open and one to close; mode 2, which it would take for good
# from relays not interlocked as it starts (Interlock starts it again, once
# shutters are on), is one for power and one for direction, which would run
# both windings to close. So the interlock goes first, and the mode is given.
TASMOTA_COMMANDS = ("Interlock 1,2", "Interlock 1", "SetOption80 1", "ShutterRelay1 1", "ShutterMode 1",
                    "PowerOnState 0")
# Tasmota's commands for the travel times, which it would start at 10 s.
TASMOTA_OPEN, TASMOTA_CLOSE = "ShutterOpenDuration1", "ShutterCloseDuration1"
TASMOTA_TRAVEL = f"{TASMOTA_OPEN} and {TASMOTA_CLOSE}"
# The ESPHome build's settings, by their names in devices/sonoff-dualr3/esphome-motor.yaml.
ESPHOME_OPEN, ESPHOME_CLOSE, ESPHOME_REVERSE = "Open duration", "Close duration", "Reverse direction"
ESPHOME_TRAVEL = f"{ESPHOME_OPEN} and {ESPHOME_CLOSE}"
# Tasmota's codes for relays 1 and 2 in a template. Its shutter opens with
# relay 1 and closes with relay 2, and has no setting of its own to swap them:
# a motor whose outputs are reversed has them swapped in its template.
TASMOTA_RELAY1, TASMOTA_RELAY2 = 224, 225
# How long Tasmota is given to have taken the commands, asked again meanwhile.
TASMOTA_TAKES = 10.0


@dataclass(frozen=True)
class Setup:
    """What a motor is set to: its travel times (seconds) and whether its
    outputs are reversed, output 2 opening. None where it is not known."""
    open: float | None = None
    close: float | None = None
    reversed: bool | None = None

    def over(self, other: Setup) -> Setup:
        """This setup, `other`'s where this does not know."""
        pick = lambda mine, theirs: theirs if mine is None else mine   # noqa: E731
        return Setup(pick(self.open, other.open), pick(self.close, other.close),
                     pick(self.reversed, other.reversed))

    def timed(self) -> bool:
        return self.open is not None and self.close is not None


DEFAULT = Setup(DEFAULT_TRAVEL, DEFAULT_TRAVEL, False)


def travel(seconds: Any) -> float | None:
    """`seconds` as a travel time, to a tenth: None for none, or one outside
    TRAVEL_RANGE."""
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        return None
    low, high = TRAVEL_RANGE
    return round(float(seconds), 1) if low <= seconds <= high else None


def sonoff_setup(settings: dict[str, Any] | None) -> Setup:
    """What Sonoff's user settings set the motor to: the travel its
    calibration timed, only once it says it is calibrated, and its outputs'
    way round."""
    settings = settings or {}
    timed = travel(settings.get("calibrate_time", 0) * SONOFF_TRAVEL_UNIT) \
        if settings.get("motorCalibrate") is True and isinstance(settings.get("calibrate_time"), int) else None
    reverse = settings.get("motorDriverReverse")
    return Setup(timed, timed, reverse if isinstance(reverse, bool) else None)


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


def tasmota_gpio(kind: kinds.Kind, reversed: bool) -> list[int]:
    """The kind's template's pins, relays 1 and 2 swapped where `reversed`."""
    swap = {TASMOTA_RELAY1: TASMOTA_RELAY2, TASMOTA_RELAY2: TASMOTA_RELAY1} if reversed else {}
    return [swap.get(code, code) for code in kind.tasmota_gpio]


def tasmota_template(kind: kinds.Kind, reversed: bool) -> str:
    """The kind's template, as the Template command takes it, for a motor
    whose outputs are `reversed` or not."""
    template = json.loads(kind.tasmota_template())
    template["GPIO"] = tasmota_gpio(kind, reversed)
    return json.dumps(template, separators=(",", ":"))


def tasmota_reversed(device: Any, kind: kinds.Kind) -> bool | None:
    """Whether a Tasmota's template has the kind's relays swapped: None for
    one that does not answer, or whose template is neither way round."""
    try:
        gpio = (device.command("Template") or {}).get("GPIO")
    except (DeviceError, OSError, AttributeError):
        return None
    for reversed in (False, True):
        if gpio == tasmota_gpio(kind, reversed):
            return reversed
    return None


def tasmota_travel(device: Any) -> tuple[float | None, float | None]:
    """A Tasmota's shutter's travel times, open and close."""
    times = []
    for name in (TASMOTA_OPEN, TASMOTA_CLOSE):
        try:
            answer = device.command(name)
        except (DeviceError, OSError):
            answer = None
        times.append(travel(answer.get(name)) if isinstance(answer, dict) else None)
    return times[0], times[1]


def tasmota_setup(device: Any, kind: kinds.Kind) -> Setup:
    """What a Tasmota sets its shutter to."""
    return Setup(*tasmota_travel(device), tasmota_reversed(device, kind))


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


def esphome_setup(host: str) -> Setup:
    """What the motor's ESPHome build sets its cover to. Its web server gives
    a number's value as text ("3.0"), a switch's as true or false."""
    def seconds(name: str) -> float | None:
        value = esphome.entity(host, "number", name)
        try:
            return travel(float(value)) if isinstance(value, (str, int, float)) else None
        except ValueError:
            return None
    reverse = esphome.entity(host, "switch", ESPHOME_REVERSE)
    return Setup(seconds(ESPHOME_OPEN), seconds(ESPHOME_CLOSE), reverse if isinstance(reverse, bool) else None)


def running_setup(ctx: Context, firmware: str) -> Setup:
    if firmware == "tasmota":
        return tasmota_setup(ctx.device, kinds.of_run(ctx.state))
    host = ctx.state.input("device_ip")
    return esphome_setup(host) if firmware == "esphome" and host else Setup()


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
            setup = running_setup(ctx, firmware)
            ctx.state.set_input("motor_setup", {"open": setup.open, "close": setup.close,
                                                "reversed": setup.reversed})
            output.log_only(f"    {firmware} sets the motor to {setup}")


def drives(ctx: Context) -> bool:
    """Whether the run's device drives a motor: its Sonoff backup says so, or
    the firmware it ran as the run began (note_running)."""
    if not kinds.of_run(ctx.state).motor_mode:
        return False
    return bool(ctx.state.input("motor_running")) or bool(sonoff_motor(backup_settings(ctx)))


def motor_setup(ctx: Context) -> Setup:
    """What the motor is to be set to, wherever the device goes: the firmware
    it ran as the run began (note_running), else its Sonoff backup, else
    DEFAULT."""
    noted = ctx.state.input("motor_setup") or {}
    running = Setup(travel(noted.get("open")), travel(noted.get("close")),
                    noted.get("reversed") if isinstance(noted.get("reversed"), bool) else None)
    return running.over(sonoff_setup(backup_settings(ctx))).over(DEFAULT)


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


def tell_set_up(firmware: str, travel_names: str, setup: Setup, timed: bool) -> None:
    """What the person is told of a device set up for its motor: its travel
    times, and to time them where they were not known (`timed`)."""
    output.tell(f"    Motor mode carried across: {firmware} drives the motor, its two outputs never on together "
                "and both off at every start" + (", output 2 opening as before." if setup.reversed else "."))
    if timed:
        output.tell(f"    Its travel times carried across: {setup.open:g} s to open, {setup.close:g} s to close "
                    f"({travel_names}).")
    else:
        output.tell(f"    Time the motor's travel and set {travel_names}: {DEFAULT_TRAVEL:g} s until you do.")


def timed(ctx: Context) -> bool:
    """Whether the run knows the motor's travel times: not DEFAULT_TRAVEL's."""
    noted = ctx.state.input("motor_setup") or {}
    return (Setup(travel(noted.get("open")), travel(noted.get("close"))).timed()
            or sonoff_setup(backup_settings(ctx)).timed())


def set_up_tasmota(ctx: Context) -> dict[str, Any]:
    """A Tasmota on a device that drives a motor set up for it, once its
    template is in place: its relays swapped there for a motor whose outputs
    are reversed (motor_setup), then TASMOTA_COMMANDS and its travel times,
    each seen taken; nothing for one in relay mode. One that has every
    setting already is left as it is, the person's travel times with it."""
    if not drives(ctx):
        return {}
    log_sonoff_motor(ctx)
    device = ctx.device
    if tasmota_in_force(device):
        output.say("    Tasmota drives the motor already: its outputs interlocked, both off at every start.")
        return {"motor": "in place"}
    setup, kind = motor_setup(ctx), kinds.of_run(ctx.state)
    if tasmota_reversed(device, kind) is (not setup.reversed):
        from tools.autoflash.routes.espressif import tasmota   # which imports this
        tasmota.give_template(device, tasmota_template(kind, bool(setup.reversed)), None)
        device = ctx.device
        if tasmota_reversed(device, kind) is not setup.reversed:
            raise Abort("Tasmota did not take the template that drives the motor the right way round; "
                        "it is safe to do this step again")
    device.command("Backlog " + "; ".join([*TASMOTA_COMMANDS, f"{TASMOTA_OPEN} {setup.open:g}",
                                           f"{TASMOTA_CLOSE} {setup.close:g}"]), timeout=30.0)
    started = time.monotonic()
    while not (tasmota_in_force(device) and tasmota_travel(device) == (setup.open, setup.close)):
        if time.monotonic() - started > TASMOTA_TAKES:
            raise Abort("Tasmota did not take the settings that keep the motor's two outputs from both being "
                        "on; it is safe to do this step again")
        time.sleep(1)
    tell_set_up("Tasmota", TASMOTA_TRAVEL, setup, timed(ctx))
    return {"motor": "set up"}


def set_up_esphome(ctx: Context) -> dict[str, Any]:
    """The motor's ESPHome build given the motor's travel times and way round
    (motor_setup), each read back."""
    host, setup = ctx.state.input("device_ip"), motor_setup(ctx)
    for _ in range(3):
        esphome.set_number(host, ESPHOME_OPEN, setup.open)
        esphome.set_number(host, ESPHOME_CLOSE, setup.close)
        esphome.set_switch(host, ESPHOME_REVERSE, bool(setup.reversed))
        if esphome_setup(host) == setup:
            tell_set_up("ESPHome", ESPHOME_TRAVEL, setup, timed(ctx))
            return {"motor": "set up"}
        time.sleep(2)
    raise Abort("ESPHome did not take the motor's travel times and direction; run the tool again before the "
                "motor is used")
