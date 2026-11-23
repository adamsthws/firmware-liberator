"""A device back after a write, on either chip family: looked for, and where
it has not come back, the person at the keyboard asked to switch it off and
on, then looked for again (until_back) - counted for the report; an
unattended run stops there, saying to.  Where Sonoff's firmware sent the
image, whether it still answers is looked for first (still_on_sonoff): if it
does, the image was not installed.  And the check every route back to
Sonoff's firmware ends with (back_on_sonoff): the device on the Wi-Fi it was
on, and in eWeLink again.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any, Callable, TYPE_CHECKING, TypeVar

from tools.autoflash import account, detect, hostsys, output, prompts, records
from tools.autoflash.errors import Abort, GoBack
from tools.ewelink import relay as ewelink_relay
from tools.ewelink.get_device_key import cloud_turn, own_time

if TYPE_CHECKING:
    from tools.autoflash.context import Context

# The emulated device's power switch, where the run has one: a file holding off or
# on (tools/beken/emulator/beken-emu.sh computer), offered as a choice of its own.
EMULATED_POWER = "AUTOFLASH_EMULATOR_POWER"
EMULATED_OFF = 3.0   # seconds off: long enough for the emulator to stop the device

T = TypeVar("T")

# What until_back says of a device that has not come back, and asks.
NOT_BACK_LINES = (
    "It has not come back on its new firmware yet. Switch it off for a few",
    "seconds at the socket, then on: it then starts the new firmware.",
)


# What until_back says in place of NOT_BACK_LINES where Sonoff's firmware,
# which sent the image, no longer answers either (still_on_sonoff).
NEITHER_LINES = (
    "Sonoff's firmware no longer answers either, so the device most likely has",
    "it, still starting or unable to join your Wi-Fi. Switch it off for a few",
    "seconds at the socket, then on, to start it again.",
)
# The run's record of what still_on_sonoff found the last time it looked,
# in the words its report sends (report_data.stock_check): Sonoff's firmware
# answering, so the image was not installed; neither answering; or not to be
# told. Unset where it never looked.
STOCK_CHECK = "stock_check"
STOCK_CHECKS = {True: "sonoff-answers", False: "neither-answers", None: "cannot-tell"}


def not_installed(what: str) -> str:
    """Why a run goes back to send `what` again, or stops, where Sonoff's
    firmware still answers after it was sent (until_back)."""
    return f"{what} was not installed: the device still answers on Sonoff's firmware"


class NotBack(Exception):
    """The device did not answer as its new firmware in the time waited.
    `lines`, where given, say why in place of NOT_BACK_LINES, and `reason`
    is what an unattended run stops with in place of the step's advice."""

    def __init__(self, reason: str = "", lines: tuple[str, ...] = ()) -> None:
        super().__init__(reason)
        self.reason, self.lines = reason, lines


class NotTaken(Exception):
    """Restarted, the device started the firmware it was leaving: the image it
    was sent was not installed, and will not be by waiting."""


def waiting_for(what: str) -> str:
    """What a wait for `what` (a firmware's name) to answer on the device says
    while it lasts (until_back)."""
    return f"Waiting for {what} to start on the device and answer - it can take a few minutes"


def until_back(ctx: Context, look: Callable[[bool], T], what: str, advice: str,
               sent_by: str | None = None, *, from_sonoff: bool = False) -> T:
    """look(restarted), and again each time the person at the keyboard says
    the device is back on after a restart - `restarted` then True - until it
    finds the device or they stop (q).  An unattended run stops at the first
    NotBack, with `advice` as the reason.  Each switch off and on is counted,
    by whom (power_cycles), for the report: normally none is needed, as
    every firmware restarts by itself once it has its update.

    `sent_by`: the step that sent the image.  A look after a restart that
    finds the device on the firmware it was leaving (NotTaken) sends the run
    back to that step, unasked: the bootloader installs a whole image on the
    first start after it arrived, so one not installed then never will be.
    Before a restart that says nothing: the device may not have restarted.
    A switch off and on asked for is remembered in the run's record
    (restart_asked), so a run that stopped to ask - an unattended one, or a
    person who quit there - takes it as done when it is run again.

    `from_sonoff`: the image was sent through Sonoff's firmware.  Not back,
    whether Sonoff's firmware still answers is looked for before the person
    is asked (still_on_sonoff).  If it does, the image was not installed -
    its updater refused it, or never applied it - and a switch off and on
    would not install it: the run goes back to `sent_by`, as after a restart,
    or with none stops, saying to try again.  If it does not, the person is
    told the device most likely has the image (NEITHER_LINES); where that
    cannot be told, asked as before.  Seen in real runs: people stopped at
    the question without switching the device, and nothing showed which it
    was."""
    restarted = bool(ctx.state.input("restart_asked"))
    asked_back = None   # when the person said it was back on, to time its return
    while True:
        try:
            # Said under the stage's line once it has lasted a moment (output.working):
            # minutes of a spinner alone, before the run asks to switch it off and
            # on, looked as if nothing was happening.
            with output.working(waiting_for(what)):
                found = look(restarted)
        except NotTaken as exc:
            ctx.state.set_input("restart_asked", None)
            if sent_by is None:
                raise Abort(advice) from exc
            raise GoBack(sent_by, f"{what} was not installed: the device restarted on the firmware it had",
                         "not installed") from exc
        except NotBack as exc:
            ctx.state.set_input("restart_asked", True)
            on_sonoff = still_on_sonoff(ctx) if from_sonoff else None
            if on_sonoff:
                ctx.state.set_input("restart_asked", None)
                if sent_by is None:
                    raise Abort(f"{not_installed(what)}, as before: try again, and it is sent again") from exc
                raise GoBack(sent_by, not_installed(what), "not installed") from exc
            if prompts.UNATTENDED:
                raise Abort(exc.reason or advice) from exc
            lines = exc.lines or (NEITHER_LINES if on_sonoff is False else NOT_BACK_LINES)
        else:
            ctx.state.set_input("restart_asked", None)
            if asked_back is not None:
                # For the report: the longest a device took to answer once back on.
                took = round(time.monotonic() - asked_back)
                ctx.state.set_input("back_after_power_cycle", max(took, ctx.state.input("back_after_power_cycle") or 0))
            return found
        power_cycle(ctx, f"{what} has not answered yet", lines, "look for it again")
        restarted = True
        asked_back = time.monotonic()


def power_cycle(ctx: Context, title: str, lines: tuple[str, ...], then: str) -> None:
    """The person at the keyboard asked to switch the device off and on, and
    to say when it is back on, for the run to `then` ("look for it again") -
    or, in the emulator, to have it switched; q stops the run (QuitRun).
    Each is counted, by whom (power_cycles), for the report."""
    output.transient_begin()
    prompts.section(title, list(lines))
    options = [f"{then[0].upper()}{then[1:]} - the device is back on"]
    switch = os.environ.get(EMULATED_POWER)
    if switch:
        options.append(f"Power-cycle the emulated device, then {then}")
    chosen = prompts.ask_choice("Once it is back on, choose", options)
    output.transient_end()
    counted = dict(ctx.state.input("power_cycles") or {})
    who = "by the tool" if chosen == 1 else "by hand"
    counted[who] = counted.get(who, 0) + 1
    ctx.state.set_input("power_cycles", counted)
    if chosen == 1:
        power_cycle_emulated(Path(switch))


def power_cycle_emulated(switch: Path) -> None:
    """The emulated device switched off, then on, as at the socket."""
    switch.write_text("off\n", encoding="utf-8")
    time.sleep(EMULATED_OFF)
    switch.write_text("on\n", encoding="utf-8")
    output.say("    Power-cycled the emulated device.")


# How long Sonoff's firmware may take to rejoin the Wi-Fi and show online in
# eWeLink before the person is asked to switch the device off and on: the
# longer of the waits each family had (the Espressif route's).
SONOFF_WAIT = 240.0
# Between looks: each asks eWeLink for the account's devices.
SONOFF_LOOK_EVERY = 10.0

ON_WIFI_ONLY_LINES = (
    "It is back on your Wi-Fi but not in eWeLink: check your network lets it",
    "online (a firewall, guest or parental rule), then switch it off and on.",
)
ON_WIFI_ONLY = ("Sonoff's firmware is on your Wi-Fi but not in eWeLink: let the device reach the internet, "
                "switch it off and on, and check the eWeLink app")
# It rejoins the network it was on and reports to eWeLink by itself.
NOT_BACK = ("Sonoff's firmware has not come back on your Wi-Fi or in eWeLink: if the app still shows it "
            "offline, switch it off and on and try again (if its network changed, add it "
            "again in the app)")


# Asked of the person when a send is refused as the device being busy
# (ota_push.busy): with an update cut off part way, until it restarts.
BUSY_LINES = (
    "The device turned it down, still busy with an update that was cut off.",
    "Switch it off and on at the socket; it keeps its Sonoff firmware.",
)
BUSY = ("the device turned the firmware down, busy with an update cut off part way: switch it off and on "
        "(it keeps its Sonoff firmware), then try again")


def online_in_ewelink(ctx: Context, device_id: str, wait: float = SONOFF_WAIT) -> bool:
    """Whether eWeLink shows the device online within `wait`, as it does a
    minute or so after it is switched back on: a send through eWeLink reaches
    it only then.  False at once where eWeLink cannot be reached.  Time spent
    waiting for a turn in the fault suite is not counted (own_time)."""
    started = own_time()
    while True:
        devices = ctx.account_devices()
        if any(found.get("deviceid") == device_id and found.get("online") for found in devices or []):
            output.progress_clear()
            return True
        if devices is None or own_time() - started >= wait:
            output.progress_clear()
            return False
        output.progress(f"waiting for eWeLink to show the device online... {own_time() - started:.0f}s")
        time.sleep(SONOFF_LOOK_EVERY)


def on_wifi(ctx: Context, running: str | None) -> bool:
    """Whether the device answers on the Wi-Fi as Sonoff's firmware (`running`,
    as detect.identify has it): by eWeLink's LAN port, which it need not open
    (LAN Control off in the app), else by its hardware address answering, as
    nothing else it ran."""
    return running == "sonoff" or (running is None and ctx.finder.poll() is not None)


def answering(ctx: Context, device_id: str) -> tuple[list[dict[str, Any]] | None, bool, bool]:
    """The account's devices (None: eWeLink not reached), whether eWeLink shows
    the device online, and whether it answers there (relay.answers) - in one
    turn of the run's own in the fault suite (cloud_turn), where the device
    reaches eWeLink only in its turn."""
    with cloud_turn():
        devices = ctx.account_devices()
        online = any(found.get("deviceid") == device_id and found.get("online") for found in devices or [])
        return devices, online, online and ewelink_relay.answers(account=ctx.ewelink, device_id=device_id) is True


def sonoff_answers(ctx: Context, host: str) -> bool:
    """Whether Sonoff's own firmware answers at `host`, for certain: by
    eWeLink's LAN port, which it need not open (LAN Control off in the app) -
    else, answering as nothing this tool knows, by the device at that hardware
    address answering through eWeLink, as no other firmware it runs can. For a
    wait that must not take a device starting another firmware for Sonoff's."""
    running = detect.identify(host, timeout=3.0)
    if running is not None:
        return running == "sonoff"
    seen = hostsys.mac_of(host)
    wanted = ctx.state.input("device_mac")
    if not seen or (wanted and hostsys.normalise_mac(seen) != wanted):
        return False
    device_id = account.device_id(ctx, hostsys.normalise_mac(seen))
    return bool(device_id) and answering(ctx, device_id)[2]


def still_on_sonoff(ctx: Context) -> bool | None:
    """After an image Sonoff's firmware was sent has not answered in the time
    waited: whether Sonoff's firmware answers instead.  True where it does,
    on the Wi-Fi (eWeLink's LAN port) or through eWeLink (relay.answers);
    False where nothing answers as Sonoff's at the device's address and
    eWeLink, reached, has the device not answering; None where that cannot
    be told - eWeLink not signed in to in this run (a sign-in asked for here
    would be a question before the question), not reached, or the device's
    id there not known.  Logged and kept in the run's record (STOCK_CHECK),
    not said on the screen: what the run does with it says."""
    with output.working("Checking whether Sonoff's firmware still answers"):
        host = ctx.finder.poll() or ctx.state.input("device_ip")
        running = detect.identify(host, timeout=3.0) if host else None
        found, why = None, "eWeLink not signed in to in this run"
        if running == "sonoff":
            found, why = True, "on your Wi-Fi"
        elif getattr(ctx.ewelink, "held", None) is not None:
            device_id = account.device_id(ctx)
            devices, online, answered = answering(ctx, device_id) if device_id else (None, False, False)
            if answered:
                found, why = True, "through eWeLink"
            elif devices is not None:
                found, why = False, f"eWeLink has the device {'online, not answering' if online else 'offline'}"
            else:
                why = "eWeLink not reached" if device_id else "its eWeLink id not known"
    ctx.state.set_input(STOCK_CHECK, STOCK_CHECKS[found])
    output.log_only(f"    Sonoff's firmware: {STOCK_CHECKS[found]} ({why}; at its address on your Wi-Fi, "
                    f"{running or 'nothing known'}).")
    return found


def back_on_sonoff(ctx: Context, leaving: tuple[str, ...] = (), wait: float = SONOFF_WAIT) -> dict[str, Any]:
    """The device on Sonoff's firmware again (R2, K3): on the Wi-Fi it was on,
    and shown online in eWeLink, answering there - so not online still from
    before it left.  Where its eWeLink id is not known here, the Wi-Fi alone
    says.  Seen on the Wi-Fi but never in eWeLink, the person is told its
    network may keep it from the internet.  Still answering as one of
    `leaving`, the firmware it was to leave, Sonoff's was not installed.
    Time spent waiting for a turn in the fault suite is not counted
    (own_time)."""
    device_id = str(records.read_device(ctx.state.input("device_mac") or "").get("ewelink_id") or "")

    def look(_restarted: bool) -> dict[str, Any]:
        started = own_time()
        seen: float | None = None   # when it first answered on the Wi-Fi
        reached = False   # eWeLink answered this computer at all
        while own_time() - started < wait:
            ctx.finder.poll()   # where it is now, if it moved
            host = ctx.state.input("device_ip")
            running = detect.identify(host, timeout=3.0) if host else None
            if running in leaving:
                output.progress(f"the device still runs the firmware it is leaving... "
                                f"{own_time() - started:.0f}s")
            else:
                if seen is None and on_wifi(ctx, running):
                    seen = own_time()
                    output.progress_clear()
                    output.say(f"    Sonoff's firmware answered on your Wi-Fi after {seen - started:.0f}s.")
                    if not device_id:
                        return {"seconds": round(seen - started, 1), "confirmed_by": "your Wi-Fi"}
                devices, online, answered = answering(ctx, device_id) if device_id else (None, False, False)
                reached = reached or devices is not None
                if answered:
                    output.progress_clear()
                    seconds = round(own_time() - started, 1)
                    output.say(f"    eWeLink shows the device online again, and it answers, after {seconds}s.")
                    return {"seconds": seconds, "confirmed_by": "eWeLink",
                            **({"on_wifi_after": round(seen - started, 1)} if seen is not None else {})}
                output.progress(f"waiting for {'eWeLink to show the device online' if seen else 'the device'}"
                                f"{', and for it to answer' if online else ''}... {own_time() - started:.0f}s")
            time.sleep(SONOFF_LOOK_EVERY)
        output.progress_clear()
        host = ctx.state.input("device_ip")
        if host and detect.identify(host, timeout=3.0) in leaving:
            raise Abort("the device still answers as the firmware it was to leave: Sonoff's firmware was not "
                        "installed. Try again, and it is sent again")
        if seen is not None and not reached:
            output.tell("    eWeLink is unreachable from here: check the eWeLink app shows the device.")
            return {"seconds": round(seen - started, 1), "confirmed_by": "your Wi-Fi"}
        if seen is not None:
            raise NotBack(ON_WIFI_ONLY, ON_WIFI_ONLY_LINES)
        if not device_id:
            # Its eWeLink device id is not known here, to look for it there.
            output.tell("    It has left its old firmware; check the eWeLink app shows it.")
            return {"seconds": round(own_time() - started, 1), "confirmed_by": "nothing answering"}
        raise NotBack

    return until_back(ctx, look, "Sonoff's firmware", NOT_BACK)
