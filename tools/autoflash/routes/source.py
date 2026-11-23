"""What the device runs when a run starts, whatever its chip family: found on
the network by what it answers as, or known from where an earlier run left
it.  It is asked before the family is known - an OpenBeken is found as a
Tasmota is - so is shared; where an Espressif device was left part way is
Espressif's to say (routes/espressif/source.py).
"""

from __future__ import annotations

import functools
import time
from typing import TYPE_CHECKING

from tools.autoflash import detect, esphome, hostsys, kinds, output, records, prompts, runs, unlock
from tools.autoflash.device import PASSWORD_HINT
from tools.autoflash.errors import Abort
from tools.autoflash.routes.espressif import source as espressif_source
from tools.autoflash.state import RunState

if TYPE_CHECKING:
    from tools.autoflash.context import Context


# The question when more than one device answers on the network; the
# attended suite answers it by this name.
CHOOSE = "Which device is this run for"


def known_macs() -> set[str]:
    """The MACs of the devices this computer has records of: their backups and their runs."""
    return {name.replace("-", ":") for name in
            (path.name for path in records.DEVICES.glob("*")) if records.MAC_RE.match(name)}


def find_current_firmware(ctx: Context) -> str:
    """What the device runs now: Tasmota, ESPHome or OpenBeken if it is one this
    tool can find converted on the network, else Sonoff's firmware (found
    later, through eWeLink).  With --device-ip, the device at that address."""
    given = ctx.state.input("device_ip")
    if given:
        running = identify_once_restarted(ctx, given)
        if running == "locked":
            unlock.ask_web_password(ctx, given)
            running = unlock.identify(ctx, given)
        elif running == "esphome-login":
            unlock.ask_esphome_login(ctx, given)
            running = unlock.identify(ctx, given)
        mover = running == "mover"
        if running in ("mover", "startup", "hub"):
            running = "midway"
        mac = hostsys.mac_of(given)
        if running is None and mac and hostsys.normalise_mac(mac) in known_macs() \
                and detect.answers_as_tasmota(given):
            # A Tasmota without the device's template, as the search finds it.
            running = "untemplated"
        if running in ("openbeken", "interim"):
            # Its kind is its import module's to say, as the search finds it.
            found = detect.openbeken_device(given)
            if not found or found.firmware != running:
                raise Abort(f"the device at {given} did not say twice alike what it runs: try again")
            found.mac = mac
            adopt_device(ctx, found)
            return running
        if running == "installer":
            left_by = installer_left_by(ctx, given, mac)
            if left_by:
                running = installer_back(ctx, given, left_by)
        if running == "readout":
            # On no route: settle_route puts Sonoff's firmware back.
            adopt_device(ctx, detect.Found(given, running, mac))
            return running
        if running is None and mac is None:
            # Nothing there at all, not even on the network, so not a device on
            # Sonoff's firmware to ask eWeLink about (seen on a real device switched
            # off: the run went on to ask eWeLink, and its report said it was
            # converting from Sonoff). Sonoff's firmware may answer nothing here
            # but is on the network, so is still asked of eWeLink.
            raise Abort(f"nothing answers at {given}: check the device is switched on and on your Wi-Fi, "
                        "then try again")
        if running in ("tasmota", "esphome", "midway", "installer", "untemplated", "esphome-update"):
            adopt_device(ctx, detect.Found(given, running, mac))
            if mover or running == "esphome-update":
                running = esphome_safe_mode(ctx, running)
            return espressif_source.past_the_commit(ctx, running)
        return "sonoff"
    known = known_macs()
    # The network this computer's internet goes through first; its other
    # Ethernet and Wi-Fi networks, read once, only if no device is there.
    lan = functools.cache(hostsys.lan_networks)

    def search() -> list[detect.Found]:
        try:
            return detect.converted_devices(hostsys.local_ip_towards("192.0.2.1"), others=lan,
                                          known_macs=known, password=ctx.web_password,
                                          esphome_login=ctx.esphome_login)
        except OSError:
            return []

    def open_ones(found: list[detect.Found]) -> list[detect.Found]:
        return [device for device in found if device.firmware not in unlock.LOCKED]

    # On screen while it looks - 5-10 s, before any stage line and its clock -
    # with the stages' spinner, and taken off again once it has looked.
    with output.working("Looking for supported devices on your network..."):
        found = search()
    if not open_ones(found) and converted_here(ctx):
        # A device this computer converted, or left part way, may be restarting -
        # a run stopped and started again at once finds it mid-restart.
        with output.working(f"None answered: looking again for {RESTART_GRACE:.0f} s, in case one is restarting..."):
            started = time.monotonic()
            while not open_ones(found) and time.monotonic() - started < RESTART_GRACE:
                time.sleep(5)
                found = search()
    # A Tasmota with a web password, or an ESPHome with a web login, says
    # whether it is a device this tool converts only once given it: offered,
    # and it asked for if chosen, when someone can type it.
    locked = [device for device in found if device.firmware in unlock.LOCKED]
    found = open_ones(found)
    if locked and prompts.UNATTENDED:
        ours = [device for device in locked if device.mac and hostsys.normalise_mac(device.mac) in known]
        if ours and not found:
            # A device this computer converted: the one the run is for, surely.
            raise Abort(unlock.locked_out(ctx, ours[0]))
        for kind, given, what, hint in (("locked", ctx.web_password, "password", PASSWORD_HINT),
                                        ("esphome-login", ctx.esphome_login, "login", unlock.ESPHOME_LOGIN_HINT)):
            hosts = [device.host for device in locked if device.firmware == kind]
            if hosts:
                output.tell(f"    Left out, for its web {what}{' (not the one given)' if given else ''}: "
                     f"the {'Tasmota' if kind == 'locked' else 'ESPHome'} at {', '.join(hosts)}. "
                     f"If it is the device, {hint}.")
        locked = []
    if not found and not locked:
        return "sonoff"
    stock = "A device still on Sonoff's firmware (it is set up in the eWeLink app)"
    if prompts.UNATTENDED and len(found) == 1:
        chosen = found[0]
        prompts.answered_unattended(CHOOSE, chosen.describe())
    else:
        offered = found + locked
        output.transient_begin()
        choice = prompts.ask_choice(CHOOSE,
                            [found_device.describe()[0].upper() + found_device.describe()[1:]
                             for found_device in offered] + [stock])
        output.transient_end()
        if choice == len(offered):
            return "sonoff"
        chosen = offered[choice]
        if chosen.firmware in unlock.LOCKED:
            chosen = unlock.unlocked(ctx, chosen)
    if chosen.firmware == "installer":
        left_by = installer_left_by(ctx, chosen.host, chosen.mac)
        if left_by:
            return installer_back(ctx, chosen.host, left_by)
    adopt_device(ctx, chosen)
    if (chosen.firmware == "midway" and chosen.name == "the mover") or chosen.firmware == "esphome-update":
        return espressif_source.past_the_commit(ctx, esphome_safe_mode(ctx, chosen.firmware))
    return espressif_source.past_the_commit(ctx, chosen.firmware)


# ESPHome started ten times in a row, each stopped within a minute, takes that
# for a boot loop and starts in safe mode: Wi-Fi and its update service, no
# page and no relay - on an Espressif device, all the mover has. Five minutes
# on, it restarts as itself. Alike on a Beken device, its ESPHome on LibreTiny
# (docs/beken-research.md#esphome-in-safe-mode).
ESPHOME_SAFE_MODE_WAIT = 400.0


def left_on_esphome(ctx: Context) -> bool:
    """Whether this computer's latest run of the device that wrote to it left
    it on ESPHome, checked: its route to ESPHome done to its last step (I2 on
    an Espressif device, K2 on a Beken one). One past that, the mover is gone
    for good, and a Beken device has none."""
    from tools.autoflash import routes  # the families' routes, which import this module
    mac = ctx.state.input("device_mac")
    for directory in reversed(runs.run_directories()):
        if directory.resolve() == ctx.dir.resolve() or records.mac_of_run(directory) != mac:
            continue
        try:
            state = RunState.open(directory)
        except (OSError, ValueError):
            continue
        if runs.wrote_nothing(state, routes.family_of(state).DESTRUCTIVE_STEPS):
            continue
        steps = routes.steps_of(state)
        return runs.run_firmware(state) == "esphome" and bool(steps) and state.is_done(steps[-1][0])
    return False


def esphome_safe_mode(ctx: Context, running: str) -> str:
    """What a device answering only ESPHome's update service runs - "midway",
    an Espressif device's, as the mover does; "esphome-update", a Beken
    device's: ESPHome in safe mode, if a run left it on ESPHome, waited out
    until it restarts as itself. Taken for part way, an Espressif one was sent
    the startup firmware, even to stay on ESPHome; a Beken one, found by
    nothing else, was taken for one on Sonoff's firmware."""
    if not left_on_esphome(ctx):
        if running == "esphome-update":
            raise Abort("the device answers only ESPHome's update service, and has not started ESPHome "
                        "as itself - in safe mode, say. Leave it switched on for ten minutes, then try again")
        return running
    host = ctx.state.input("device_ip")
    output.say("    It answers only ESPHome's update service, though this computer left it on")
    output.say("    ESPHome: ESPHome in safe mode, after starting ten times in a row, each")
    output.say("    stopped within a minute. It restarts as itself within five minutes.")
    output.say("    Waiting for it - leave it switched on...")
    started = time.monotonic()
    while time.monotonic() - started < ESPHOME_SAFE_MODE_WAIT:
        found = unlock.identify(ctx, host)
        if found == "esphome-login":
            unlock.ask_esphome_login(ctx, host)
            found = unlock.identify(ctx, host)
        if found == "esphome":
            output.say(f"    ESPHome answered after {time.monotonic() - started:.0f}s.")
            return "esphome"
        time.sleep(5)
    raise Abort(
        "the device answers only ESPHome's update service, and has not started ESPHome "
        f"in {ESPHOME_SAFE_MODE_WAIT / 60:.0f} minutes. Leave it switched on for ten "
        "minutes, then try again")


def identify_once_restarted(ctx: Context, host: str) -> str | None:
    """What the device at `host` runs.  Silent, a device this computer converted
    may be restarting - never to be taken for one on Sonoff's firmware, as
    the search below waits for it too.  A MAC says which device it is, where
    the network still shows one."""
    running = unlock.identify(ctx, host)
    if running is not None or detect.answers_as_tasmota(host):
        return running
    mac = hostsys.mac_of(host)
    if not (hostsys.normalise_mac(mac) in known_macs() if mac else converted_here(ctx)):
        return None
    # This computer converted it, so it may be restarting.
    output.tell(f"    No answer; it may be restarting - looking again for up to {RESTART_GRACE:.0f} s...")
    started = time.monotonic()
    while running is None and time.monotonic() - started < RESTART_GRACE:
        time.sleep(5)
        running = unlock.identify(ctx, host)
    return running


RESTART_GRACE = 45.0

# Where a run finds this tool's installer (the bridge) running and it is no
# conversion's to carry on: a readout's, whose bridge is the same program, or
# one no run of this computer's left. The bridge chose Sonoff's firmware for
# the next start as it started, so switching it off and on puts it back.
INSTALLER_LEFT = ("the device is on this tool's installer, left by {who}, and would not restart: switch it off "
                  "and on to go back to Sonoff (settings kept), then try again")


def installer_left_by(ctx: Context, host: str, mac: str | None) -> str | None:
    """Who left this tool's installer running at `host`, where it is no
    conversion to carry on: "a readout stopped part way", or "no run of this
    computer's". None for a conversion's: this computer's latest run on the
    device converting it and unfinished, or none at all but no Sonoff firmware
    left to start. A readout never writes into Sonoff's slot, so its installer
    always has Sonoff's firmware to fall back to: only a conversion's upload
    takes it away. So no readout's is ever carried on as a conversion, whatever
    computer ran it."""
    from tools.autoflash.routes.espressif import installer
    last = last_run(ctx, hostsys.normalise_mac(mac)) if mac else None
    if last == "conversion":
        return None
    if last == "readout":
        return "a readout stopped part way"
    if not installer.has_fallback(host):
        return None   # an upload into Sonoff's slot cut off part way: only a conversion's
    return "no run of this computer's"


def installer_back(ctx: Context, host: str, left_by: str) -> str:
    """This tool's installer at `host`, no conversion's, restarted into Sonoff's
    firmware (bridge 4.4.6): "sonoff", for the run to go on from there. Abort,
    with nothing done, where it would not restart: a person switches it off
    and on."""
    from tools.autoflash.routes.espressif import installer
    output.say(f"    The device is on this tool's installer, left by {left_by}: restarting it into its Sonoff "
               "firmware.")
    if not installer.restart_into_sonoff(ctx, host):
        raise Abort(INSTALLER_LEFT.format(who=left_by))
    output.event("phase", phase="back-done")
    output.say("    It is back on its Sonoff firmware.")
    return "sonoff"


def last_run(ctx: Context, mac: str) -> str | None:
    """What this computer's latest run on the device `mac`, before this one, was:
    "readout", or "conversion" while unfinished; None for a finished one, or
    none. A run that got nowhere - stopped before it knew where it went - is
    passed over."""
    from tools.autoflash import readout, routes
    for directory in reversed(runs.run_directories()):
        if directory.resolve() == ctx.dir.resolve() or records.mac_of_run(directory) != mac:
            continue
        try:
            state = RunState.open(directory)
        except (OSError, ValueError):
            continue
        if state.input(readout.STATE_KEY):
            return "readout"
        if state.input("source") or state.input("firmware"):
            steps = routes.steps_of(state)
            return None if steps and state.is_done(steps[-1][0]) else "conversion"
    return None


def converted_here(ctx: Context) -> bool:
    """Whether this computer has a record of converting a device, other than this run."""
    for directory in runs.run_directories():
        if directory.resolve() == ctx.dir.resolve():
            continue
        try:
            if RunState.open(directory).input("firmware"):
                return True
        except (OSError, ValueError):
            continue
    return False


def kind_left_part_way(ctx: Context) -> str | None:
    """The kind this computer's latest run of the device recorded. What a device runs
    part way - the installer, the mover, the startup firmware, the routes'
    Tasmota, a Tasmota without the device's template - looks the same on every
    kind of device, and says nothing; a run taking one for the first
    kind the tool knows sent it that chip's firmware."""
    mac = ctx.state.input("device_mac")
    for directory in reversed(runs.run_directories()):
        if directory.resolve() == ctx.dir.resolve() or records.mac_of_run(directory) != mac:
            continue
        try:
            kind = RunState.open(directory).input("device_kind")
        except (OSError, ValueError):
            continue
        if kind:
            return kind
    return None


def tasmota_kind(ctx: Context, host: str) -> str:
    """The kind official Tasmota at `host` says by its template, as the
    network search reads it (detect.tasmota)."""
    try:
        said = detect.tasmota(host, password=getattr(ctx, "web_password", None))
    except (detect.Unreadable, detect.Locked):
        said = None
    if not said or said[0] != "tasmota" or not said[2]:
        raise Abort(f"the device at {host} did not say twice alike what it runs: try again")
    return said[2]


def adopt_device(ctx: Context, found: "detect.Found") -> None:
    """This run is for the device `found`: its address, and its MAC to file it by."""
    ctx.set_device_ip(found.host)
    mac = found.mac or hostsys.mac_of(found.host)
    if mac and records.MAC_RE.match(mac):
        mac = hostsys.normalise_mac(mac)
        ctx.state.set_input("device_mac", mac)
        runs.file_device(ctx, mac, None)
    kind = found.kind
    if kind is None and found.firmware == "esphome":
        # Every kind's ESPHome looks the same but for its comment: an S61's
        # run must never take the S60's way.
        named = kinds.for_esphome(*esphome.title(found.host))
        kind = named.key if named else None
    if kind is None and found.firmware == "tasmota":
        # Found by its address, or once given its web password: what it runs
        # was all that was asked, not the template that names its kind. Left
        # unknown, the run took it for the first kind (kinds.of_run), and its
        # report named no device.
        kind = tasmota_kind(ctx, found.host)
    if kind is None and found.firmware in ("midway", "installer", "untemplated", "esphome-update") \
            and ctx.state.input("device_mac"):
        kind = kind_left_part_way(ctx)
    if kind:
        ctx.state.set_input("device_kind", kind)
    output.say(f"    This run is for {found.describe()}.")
