"""What eWeLink offers every device in the account - each file's link and
SHA-256 (readout.an_offer) - sent to the website unasked as a run goes, as
its report is (docs/what-the-tool-sends.md), so the firmware of devices no one
reads out reaches it too.

Beside the run, never in its way: a thread (start), begun once the run has
listed the account's devices, asks eWeLink for each device's latest offer,
QUERY_MOST devices a query with PAUSE between, on the run's own sign-in - never one of its own,
which would end the run's - and stops if eWeLink ends it. Its calls take
turns with the run's (get_device_key.CALLS), the run's first; an offer the run
asked for already is not asked again (account.ASKED_OFFERS). Then one request
to /offers (readout.send_offers), and only where the website asks for links
(its `send`, asked first with none: readout.website_sends), each with its
light work made in a process of its own at the lowest priority (Sender.prove),
so the screen never stutters for it. Nothing on the screen, one line in the
run's log. Dropped wherever it is as the run ends (drop), its work under way
with it: nothing is kept to send later.

Sent where the person lets reports be sent, and only where readouts may go (enabled).
Each device's offer goes again only OFFER_RESEND_DAYS after this computer
last sent it (SENT).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import subprocess
import threading
from typing import Any, Callable, TYPE_CHECKING

from tools.autoflash import account, output, prompts, proof_of_work, readout, report_data, report_upload, runs
from tools.ewelink import offered_firmware
from tools.ewelink.get_device_key import CALLS, EwelinkError, SessionEnded
from tools.ewelink.query_ota import QUERY_MOST, latest_offers, ota_identity

if TYPE_CHECKING:
    from tools.autoflash.context import Context

OFFER_RESEND_DAYS = 7
# Seconds between two of the queries eWeLink is asked, QUERY_MOST devices
# each: a large account is not asked all at once.
PAUSE = 3.0
# The day this computer last sent each device's offer, by its key (key_of),
# beside the settings: {key: "YYYY-MM-DD"}.
SENT = runs.SETTINGS_PATH.parent / "offers-sent.json"
# The website's answers that settle an offer until OFFER_RESEND_DAYS on;
# "full" does not, and goes at a later run.
SETTLED = ("taken", "have", "refused")


def enabled(ctx: Context) -> bool:
    """Whether this run sends offers: where the person lets it send its report
    (report_upload.chosen: not --no-report, AUTOFLASH_NO_REPORT or a dry run),
    never unattended, and only where a readout may go (readout.upload_url):
    the website, or a test's own server - in the emulator, its local API."""
    return report_upload.chosen(ctx) and not prompts.UNATTENDED and readout.upload_url() is not None


def key_of(device: dict[str, Any]) -> str | None:
    """A device as SENT keeps it: its hash as a report's (report_data.device_hash),
    or, where eWeLink gives no MAC, its account id hashed - never the id itself."""
    found = report_data.device_hash((device.get("params") or {}).get("staMac"))
    deviceid = str(device.get("deviceid") or "")
    return found or (hashlib.sha256(f"ewelink-device:{deviceid}".encode()).hexdigest() if deviceid else None)


def sent_before() -> dict[str, str]:
    try:
        kept = json.loads(SENT.read_text(encoding="utf-8"))
        return {str(key): str(day) for key, day in kept.items()}
    except (OSError, ValueError, AttributeError):
        return {}


def recent(day: str, today: dt.date) -> bool:
    """Whether an offer sent on `day` was sent within OFFER_RESEND_DAYS."""
    try:
        return (today - dt.date.fromisoformat(day)).days < OFFER_RESEND_DAYS
    except ValueError:
        return False


def due(device: dict[str, Any], kept: dict[str, str], today: dt.date) -> bool:
    """Whether the device's offer is to be sent: not sent from here recently."""
    key = key_of(device)
    return key is not None and not recent(kept.get(key, ""), today)


def remember(keys: list[str], today: dt.date) -> None:
    """These devices' offers sent today; those sent longer ago forgotten."""
    kept = {key: day for key, day in sent_before().items() if recent(day, today)}
    kept.update(dict.fromkeys(keys, today.isoformat()))
    SENT.parent.mkdir(parents=True, exist_ok=True)
    part = SENT.with_suffix(".part")
    part.write_text(json.dumps(kept, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    part.replace(SENT)


class NotProven(Exception):
    """The light work not made: the run ended, or its process failed."""


class Sender:
    """One run's offers, sent beside it (start)."""

    def __init__(self, ctx: Context, devices: list[dict[str, Any]], today: dt.date | None = None,
                 log: Callable[[str], None] = output.log_only) -> None:
        self.ctx = ctx
        self.devices = list(devices)
        self.today = today or dt.date.today()
        self.stop = threading.Event()
        self.done = threading.Event()
        self.log = log
        # The light work under way (prove), killed as the run ends (drop).
        self.working: subprocess.Popen[str] | None = None
        self.lock = threading.Lock()

    def say(self, text: str) -> None:
        """The run's log's one line of offers, unless the run has ended."""
        if not self.stop.is_set():
            self.log(f"Offers of the account's devices: {text}")

    def prove(self, body: bytes) -> str:
        """The light work for `body` (readout.light_post), made in a process of
        its own at the lowest priority (report_upload.work_beside), as a
        report's is: in this thread, it would hold the screen's for seconds."""
        with self.lock:
            if self.stop.is_set():
                raise NotProven("the run ended first")
            self.working = report_upload.work_beside(proof_of_work.body_subject(body), readout.LIGHT_PROOF_BITS)
        made = report_upload.proof_of(self.working)
        if made is None:
            raise NotProven("the run ended first" if self.stop.is_set() else "its work was not made")
        return made

    def end(self) -> None:
        """Stopped, with its work under way (drop)."""
        with self.lock:
            self.stop.set()
            if self.working is not None:
                report_upload.stop(self.working)

    def run(self) -> None:
        try:
            self.send()
        except Exception as error:  # noqa: BLE001 - never worth stopping a run for
            self.say(f"not sent ({type(error).__name__}: {error}).")
        finally:
            self.done.set()

    def send(self) -> None:
        kept = sent_before()
        todo = [device for device in self.devices if due(device, kept, self.today)]
        if not todo:
            self.say(f"none due (each sent within {OFFER_RESEND_DAYS} days).")
            return
        # Asked with no offers before eWeLink is asked anything: not /wanted,
        # whose every question the website counts as a device wanted.
        if readout.website_sends(prove=self.prove) != "links":
            self.say("not sent: the website takes files, not links, for now.")
            return
        found = self.latest(todo)
        if found is None:
            return   # stopped, or eWeLink ended the sign-in: said already
        offers, keys, unsaid, unlinked = [], [], 0, 0
        for device in todo:
            deviceid = str(device.get("deviceid") or "")
            latest, params, extra = found[deviceid], device.get("params") or {}, device.get("extra") or {}
            # A link of a shape not known here goes whole, for review, unless
            # it names the device (offered_firmware.for_review).
            own = tuple(str(each) for each in (deviceid, params.get("staMac")) if each)
            if "offers" in latest and offered_firmware.unlinked(latest["offers"], own):
                # An offer names all its files: one with a link that cannot be
                # sent even for review waits for a tool that knows it, unremembered.
                unlinked += 1
            elif "offers" in latest:
                offers.append(readout.an_offer(str(device.get("productModel") or ""), str(extra.get("model") or ""),
                                               str(params.get("fwVersion") or ""), latest["offers"],
                                               report_data.device_hash(params.get("staMac")), own))
                keys.append(key_of(device))
            else:
                unsaid += 1
        if self.stop.is_set():
            return
        review = sum("url" in item for offer in offers for item in offer["files"])
        left = "; ".join(part for part in (review and f"{review} of their files by a link of another shape, for review",
                                           unsaid and f"eWeLink said nothing of {unsaid}",
                                           unlinked and f"{unlinked} not sent: a link of another shape, unfit for review") if part)
        if not offers:
            self.say(f"none sent; {left}.")
            return
        sends, results = readout.send_offers(offers, prove=self.prove)
        if sends != "links":   # switched to files meanwhile: none kept
            self.say("not sent: the website takes files, not links, for now.")
            return
        remember([key for key, (result, _) in zip(keys, results) if result in SETTLED], self.today)
        counts = {word: sum(result == word for result, _ in results) for word in ("taken", "have", "full", "refused")}
        whys = "; ".join(dict.fromkeys(why for result, why in results if result == "refused" and why))
        self.say(f"{len(offers)} sent: {counts['taken']} taken, {counts['have']} held already, "
                 f"{counts['full']} for another day, {counts['refused']} turned down"
                 + (f" ({whys[:200]})" if whys else "") + (f"; {left}" if left else "") + ".")

    def latest(self, todo: list[dict[str, Any]]) -> dict[str, dict[str, Any]] | None:
        """eWeLink's latest offer to each device ({"offers"} or {"problem"}), by
        its id: the run's own where it asked already, the rest QUERY_MOST a
        query, PAUSE between two. None once the run has ended or eWeLink has
        ended its sign-in, which stops the sending."""
        found: dict[str, dict[str, Any]] = {}
        identities = []
        for device in todo:
            deviceid = str(device.get("deviceid") or "")
            if deviceid in account.ASKED_OFFERS:
                found[deviceid] = account.ASKED_OFFERS[deviceid]
                continue
            try:
                identities.append(ota_identity(device))
            except (EwelinkError, KeyError):
                found[deviceid] = {"problem": "no model or version"}
        for start in range(0, len(identities), QUERY_MOST):
            if start and self.stop.wait(PAUSE):
                return None
            with CALLS.background(self.stop) as turn:
                if not turn:
                    return None   # the run ended (drop)
                # The run's sign-in as it holds it now: none, and none is made here.
                held = getattr(getattr(self.ctx, "ewelink", None), "held", None)
                if held is None:
                    self.say("not sent: the run holds no sign-in to eWeLink.")
                    return None
                try:
                    found.update(latest_offers(held[0], held[1]["at"], identities[start:start + QUERY_MOST]))
                except SessionEnded:
                    self.say("not sent: eWeLink ended the sign-in.")
                    return None
        return found

def start(ctx: Context, devices: list[dict[str, Any]]) -> Sender | None:
    """Begin sending the account's offers beside the run, once a run (enabled),
    on the devices it has just listed."""
    if getattr(ctx, "offers", None) is not None or not devices or not enabled(ctx):
        return None
    ctx.offers = sender = Sender(ctx, devices)
    threading.Thread(target=sender.run, name="offers", daemon=True).start()
    return sender


def drop(ctx: Context) -> None:
    """As the run ends, however it ends: its offers dropped wherever they are."""
    sender: Sender | None = getattr(ctx, "offers", None)
    if sender is not None and not sender.done.is_set():
        sender.say("not sent: the run ended first.")
        sender.end()
