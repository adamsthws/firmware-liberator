"""What both fault suites, and the attended suite, judge a run by beyond its
steps being done: the end-of-run checks the report of a converting run gives,
the readout.tar a readout keeps, what the API beside the emulator was asked
and kept of a readout sent to it, and the screen each run left. A run whose steps all passed can
still end with a relay that did not switch, or a check that never got to run,
and say so only there; or leave a stage's failed try on the screen
(docs/testing.md#the-fault-suites)."""
from __future__ import annotations

import hashlib
import io
import json
import lzma
import os
import re
import shutil
import tarfile
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from tools.autoflash import output, report_upload
from tools.repo import unattended_run


def run_outcomes(captures: Path, since: float) -> list[dict]:
    """How each run of the tool under `captures` (an emulator's captures/)
    ended since `since` (seconds, the epoch's), oldest first: each run's line in
    its folder's report_upload.OUTCOMES, which the run itself wrote as it
    ended. A run killed outright wrote none."""
    found = []
    for path in Path(captures).glob(f"*/*/runs/*/{report_upload.OUTCOMES}"):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                said = json.loads(line)
            except ValueError:
                continue
            if isinstance(said, dict) and isinstance(said.get("at"), (int, float)) and said["at"] >= since:
                found.append(said)
    return sorted(found, key=lambda said: said["at"])

# As the run's log has them: the report's answer (tools/autoflash/report.py,
# checks_passed), up to the gap after it, in either column; and the line its
# check said of the relay.
CHECKS_PASSED = re.compile(r"^\[[\d:]+\] .*?\b(?:Checks passed|Checks): +(\S.*?)(?: {2,}| *$)", re.M)
RELAY = re.compile(r"^\[[\d:]+\] +Relay: +(.+?) *$", re.M)

# The answers a run ending on each firmware may give in the emulator: "Pass",
# or as a release before 3.0.0 said it, "True". On Tasmota, ESPHome and
# OpenBeken the check needs only the firmware, which the emulator runs: it
# switches the relay, so anything but a pass is a failure - "Not run" there is
# a check never tried. Back on Sonoff's it switches the relay through eWeLink,
# the stub's or the real one, by the device id the device's record keeps: "Not
# run" is a device with none (one converted before it was kept), which the
# emulator cannot change. "Fail" ("False") is a failure everywhere: the relay
# did not switch, or could not be checked, as when eWeLink had not shown the
# device online yet.
PASSED = {"Pass", "True"}
PASSING = {"tasmota": PASSED, "esphome": PASSED, "openbeken": PASSED, "sonoff": {*PASSED, "Not run"}}


def last_run(log: str) -> str:
    """The last run in a run log: one log holds every run carried on in it."""
    return log[log.rfind("=== run started"):] if "=== run started" in log else log


def last_run_events(events: list[dict]) -> list[dict]:
    """The last run's events (output.EVENTS) of a run folder's: from its start."""
    starts = [at for at, item in enumerate(events) if item["event"] == "run"]
    return events[starts[-1]:] if starts else events


def advised_to_switch(events: list[dict]) -> bool:
    """Whether a run's events (output.EVENTS) end with advice to switch the
    device off and on: since its last step began - as it stopped, not in a step
    it went on from - and not an ask to do it then, which it waited on."""
    began = max((at for at, item in enumerate(events) if item["event"] == "began"), default=-1)
    return any(item["event"] == "switch" and not item.get("now") for item in events[began + 1:])


def waits(events: Iterable[dict], since: float) -> list[str]:
    """The explained waits (output.working's "wait" events) runs went through
    from `since` (seconds since the epoch) on, each once: which a scenario
    reached, kept in its notes, as a wait seldom met shows which do."""
    return sorted({str(item.get("wait")) for item in events
                   if item["event"] == "wait" and item.get("at", 0) >= since})


# What a run says of an error the tool did not see coming (runner.unexpected).
UNEXPECTED = "something unexpected went wrong"


def unexpected(logs: Iterable[str], outcomes: Iterable[dict]) -> bool:
    """Whether a run of this checkout's tool met an error it did not see
    coming - a bug, whatever the fault, though the next run may convert the
    device (FAIL-UNEXPECTED): said in its log (`logs`, the words of this
    checkout's runs), or its own outcome classing its stop a crash, as a run
    does whatever its words (fault_verdicts.run_outcomes). A run the suite
    killed, as its crash fault does, says neither."""
    return (any(UNEXPECTED in text for text in logs)
            or any((said.get("stopped") or {}).get("error") == "crash" for said in outcomes))


# The report's Checks answer as each run's outcome has it (report_data.checks_word),
# and as the run log's report says it.
SAID = {"passed": "Pass", "failed": "Fail", "not-run": "Not run"}


def checks_problem(log: str, firmware: str, metered: bool = False, outcome: dict | None = None) -> str | None:
    """What the end-of-run checks of the last run in `log` said, when they did
    not pass for a device on `firmware`: "Checks passed: Fail; Relay: did not
    switch". None when they passed, or the run gave no report (a device already
    on the firmware asked for has nothing to check). `metered`: the emulated
    device's meter measures a load while its relay is on, so where the check
    reads the meter (all but Sonoff's firmware) it must confirm the switch.

    Read from the run's own outcome (`outcome`, its last line of
    fault_verdicts.run_outcomes, which a run that reported wrote as it did),
    as the run judged its checks; from the report's words in the log only for
    a run that kept no checks there - an earlier release's."""
    run = last_run(log)
    checks = (outcome or {}).get("checks")
    if isinstance(checks, dict) and unattended_run.LOGGED_RESULT.search(run):
        answer = SAID.get(checks.get("answer"), str(checks.get("answer")))
        said = f"Relay: {checks.get('relay') or 'not said'}"
        if answer not in PASSING.get(firmware, {*PASSED, "Not run"}):
            return f"Checks passed: {answer}; {said}"
        if metered and firmware != "sonoff" and not checks.get("meter_confirms"):
            return f"the meter did not confirm the switch; {said}"
        return None
    answers = CHECKS_PASSED.findall(run)
    relay = RELAY.findall(run)
    said = f"Relay: {relay[-1] if relay else 'not said'}"
    if not answers:
        return None
    if answers[-1] not in PASSING.get(firmware, {*PASSED, "Not run"}):
        return f"Checks passed: {answers[-1]}; {said}"
    if metered and firmware != "sonoff" and not (relay and relay[-1].endswith("(meter confirms)")):
        return f"the meter did not confirm the switch; {said}"
    return None


# What a readout keeps (docs/readouts.md, "What is in one"), by chip family:
# each .bin in it xz-compressed, and the firmware eWeLink offers under offered/.
READOUT_FILES = {"espressif": ("manifest.json", "flash.bin.xz", "efuse.bin.xz", "info.txt", "report.json"),
                 "beken": ("manifest.json", "flash.bin.xz", "info.txt")}
# The flash of every device the emulators run: 4 MB.
WHOLE_FLASH = 0x400000


def readout_problems(data: bytes, family: str) -> list[str]:
    """What a readout.tar lacks of a whole readout of a `family` device, as
    docs/readouts.md lists it, unsent from an unattended run: [] for none."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            files = {member.name: tar.extractfile(member).read() for member in tar.getmembers() if member.isfile()}
    except (tarfile.TarError, OSError) as exc:
        return [f"not a tar: {exc}"]
    problems = [f"no {name}" for name in READOUT_FILES[family] if name not in files]
    if list(files)[:1] != ["manifest.json"]:
        problems.append("manifest.json is not first")
    try:
        manifest = json.loads(files.get("manifest.json") or b"")
    except ValueError:
        return problems + ["manifest.json is not JSON"]
    # The firmware eWeLink offers: each file under offered/, or where the API
    # asked for links, named in the manifest by its link, or as held already.
    named = [item for item in manifest.get("offered") or [] if isinstance(item, dict)]
    if not any(name.startswith("offered/") for name in files) \
            and not any(item.get("link") or item.get("held") for item in named):
        problems.append("no offered firmware")
    problems += [f"{item['file']} named, not in it" for item in named if item.get("file") and item["file"] not in files]
    if manifest.get("read") is not True:
        problems.append("the manifest says the memory was not read")
    if "contact" in manifest:
        problems.append("a contact in it, from an unattended run")
    flash = manifest.get("flash") or {}
    if family == "espressif" and flash.get("matches_device") is not True:
        problems.append("the flash read does not match the device's own hash")
    try:
        size = len(lzma.decompress(files["flash.bin.xz"])) if "flash.bin.xz" in files else None
    except lzma.LZMAError:
        return problems + ["flash.bin.xz is not xz"]
    if size is not None and (size != WHOLE_FLASH or flash.get("size") != size):
        problems.append(f"flash.bin is {size:,} bytes, the manifest says {flash.get('size')}: "
                        f"not the whole {WHOLE_FLASH:,}")
    return problems



# A line of the API's requests.log (website/api/local.mjs): when, the method,
# the path, the body's length, and the answer's status and body.
API_REQUEST = re.compile(r"^\S+ (\w+) (\S+) \d+ -> (\d+) (.*)$")
# What the readout of every device says as it starts on each one (readout.py).
READING_OUT = re.compile(r"Reading out (.*) \((\d+) of (\d+)\)\.\.\.")


def api_asks(requests: str) -> tuple[list[list], list[tuple[int, str]]]:
    """The API's requests.log as the tool's readouts made it: a /wanted for
    each device, then each /readout of it sent, a 429 sent again. Each
    device's [wanted, why not, [(status, id) of each send]] - wanted as the
    tool takes it: one it could not read goes ahead - and the sends no
    /wanted came before."""
    asks: list[list] = []
    unasked: list[tuple[int, str]] = []
    for line in requests.splitlines():
        found = API_REQUEST.match(line)
        if not found:
            continue
        _, path, status, body = found.groups()
        try:
            answer = json.loads(body)
        except ValueError:
            answer = {}
        answer = answer if isinstance(answer, dict) else {}
        if path == "/v1/wanted":
            asks.append([not (status == "200" and answer.get("wanted") is False), str(answer.get("why") or ""), []])
        elif path == "/v1/readout":
            (asks[-1][2] if asks else unasked).append((int(status), str(answer.get("id") or "")))
    return asks, unasked


def readout_api_problems(run: str, finished: bool, api: Path, account: int, device: str, family: str,
                         kept: Iterable[str] = (), runs: str | None = None) -> tuple[list[tuple[str, str]], dict]:
    """What one run of a readout sent to the API beside the emulator did
    wrong, from its log (`run`), whether it finished, and what the API was
    asked and kept (`api`, its folder): [(verdict, why)], [] for nothing; and
    a summary. A readout of every device (readout --all) says each device's
    name as it starts on it; one of the device alone does not. `account` is
    how many devices the account holds - or, of the device alone, how many
    times it was read out in the sitting ("What next": read out another), `device` the emulated device's
    product model, of chip family `family`; `kept` the readouts the run kept
    unsent; `runs`, for a device on another firmware than Sonoff's (detect.NAMES'
    key), whose readout is its first stage alone (first_stage_problems). Judged by the API's own answers, never its rules: every device
    asked /wanted once, all of them in a finished run (FAIL-NOT-ASKED); every
    one wanted sent until taken - 201, after nothing but 429s - but the one a
    stopped run stopped on (FAIL-NOT-SENT); none not wanted sent
    (FAIL-SENT-UNWANTED); each taken in the API's store (FAIL-NOT-STORED),
    the emulated device's whole (FAIL-READOUT); and none kept unsent
    (FAIL-KEPT-UNSENT)."""
    problems = []
    devices = [(who, int(total)) for who, _, total in READING_OUT.findall(run)]
    every = bool(devices)
    if not every:
        devices = [(f"the device ({device})", account)] * account
    requests = api / "requests.log"
    asks, unasked = api_asks(requests.read_text(encoding="utf-8", errors="replace") if requests.exists() else "")
    if finished and (len(devices) != account or any(total != account for _, total in devices)):
        problems.append(("FAIL-NOT-ASKED", f"{len(devices)} devices read out, of the account's {account}"))
    if len(asks) != len(devices) and (finished or len(asks) != len(devices) - 1):
        problems.append(("FAIL-NOT-ASKED", f"{len(asks)} asked /wanted, of {len(devices)} devices read out"))
    stored = sorted(path.name for path in api.glob("readouts__*.tar")) if api.is_dir() else []
    whole = False
    for number, (wanted, why, sent) in enumerate(asks):
        who = devices[number][0] if number < len(devices) else f"device {number + 1}"
        statuses = [status for status, _ in sent]
        if not wanted:
            if sent:
                problems.append(("FAIL-SENT-UNWANTED", f"{who}: not wanted ({why}), sent all the same"))
            continue
        if not sent:
            if finished or number != len(asks) - 1:   # else the device the run stopped on
                problems.append(("FAIL-NOT-SENT", f"{who}: wanted, never sent"))
            continue
        if statuses[-1] != 201 or any(status != 429 for status in statuses[:-1]):
            problems.append(("FAIL-NOT-SENT", f"{who}: wanted, answered {', '.join(map(str, statuses))}"))
            continue
        # The API's id is <day>-<the readout's SHA-256, its first 12 digits>;
        # its key readouts/<day>/<the first 24>.tar.
        day, _, digest = sent[-1][1].rpartition("-")
        kept_as = [name for name in stored if day and digest and name.startswith(f"readouts__{day}__{digest}")]
        if not kept_as:
            problems.append(("FAIL-NOT-STORED", f"{who}: taken as {sent[-1][1]!r}, not in the API's store"))
        elif not every or who.endswith(f"({device})"):
            whole = True
            data = (api / kept_as[0]).read_bytes()
            missing = first_stage_problems(data, runs) if runs else readout_problems(data, family)
            if missing:
                problems.append(("FAIL-READOUT", f"{who}: {'; '.join(missing)}"))
    if unasked:
        problems.append(("FAIL-SENT-UNWANTED", f"{len(unasked)} sent before any /wanted"))
    problems += [("FAIL-KEPT-UNSENT", f"{name} kept unsent, though the API was there") for name in kept]
    summary = {"devices": len(devices), "asked": len(asks), "wanted": sum(1 for ask in asks if ask[0]),
               "taken": sum(1 for ask in asks if ask[2] and ask[2][-1][0] == 201),
               "429": sum(1 for ask in asks for status, _ in ask[2] if status == 429), "device_whole": whole}
    return problems, summary


def own_offer_problems(api: Path, product: str) -> list[tuple[str, str]]:
    """What the API beside the emulator (`api`, its folder) made of a readout
    of the `product` device sent after the offer of it the run sent beside it,
    the API taking links (the attended suite's `api_links`), from its own
    answers and store: an offer sent and taken, and in the store; each file
    it and the readout name fetched on first sight, with no approval or night
    between, and held as the file its SHA-256 names (FAIL-OFFER); and the
    readout, after its sender's own offer, wanted (FAIL-NOT-WANTED), as the
    API's THEIRS once refused it. An offer sent only after the readout asked
    /v1/wanted - the run outran the sender beside it - has not tried the case:
    NOT-FIRED, as a fault suite's fault that never happened, the offer's own
    checks left out. That the readout was then taken whole is
    readout_api_problems'. [] for nothing wrong."""
    requests = api / "requests.log"
    asked = [found.groups() for line in (requests.read_text(encoding="utf-8", errors="replace")
                                         if requests.exists() else "").splitlines()
             if (found := API_REQUEST.match(line))]

    def answer_of(body: str) -> dict:
        try:
            said = json.loads(body)
        except ValueError:
            return {}
        return said if isinstance(said, dict) else {}
    offered = [(at, answer_of(body)) for at, (_, path, _, body) in enumerate(asked) if path == "/v1/offers"]
    # The question whether to send links is an /offers with none in it, answered with none.
    sent = [(at, said) for at, said in offered if said.get("offers")]
    taken = [at for at, said in sent
             if any(isinstance(item, dict) and item.get("result") == "taken" for item in said["offers"])]
    wanted = [(at, status, body) for at, (_, path, status, body) in enumerate(asked) if path == "/v1/wanted"]
    problems = []
    # The offer that counts: the first taken, else the first sent.
    first = taken[0] if taken else sent[0][0] if sent else None
    fired = not (wanted and first is not None and first > wanted[0][0])
    if not sent:
        problems.append(("FAIL-OFFER", "none sent to /v1/offers"))
    elif not fired:
        problems.append(("NOT-FIRED", "the offer was sent only after the readout asked /v1/wanted: "
                                      "the case is not tried"))
    elif not taken:
        problems.append(("FAIL-OFFER", f"none taken: /v1/offers answered {json.dumps(sent[-1][1])[:120]}"))
    if wanted:
        _, status, body = wanted[0]
        if status != "200" or answer_of(body).get("wanted") is not True:
            problems.append(("FAIL-NOT-WANTED", f"after its own offer, /v1/wanted answered {status} {body[:120]}"))
    # Each offer kept as a first stage's manifest alone, beside what the store says of it (.meta.json).
    stored = sorted(path for path in api.glob("readouts__*.json") if not path.name.endswith(".meta.json"))
    offers = [manifest for manifest in (answer_of(path.read_text(encoding="utf-8", errors="replace"))
                                        for path in stored)
              if fired and manifest.get("read") is False
              and str(manifest.get("model") or "").upper() == product.upper()]
    if fired and taken and not offers:
        problems.append(("FAIL-OFFER", f"taken, but no offer of {product} in the API's store"))
    shas = {str(item.get("sha256")) for manifest in offers for item in manifest.get("offered") or []
            if isinstance(item, dict)}
    if offers and not shas:
        problems.append(("FAIL-OFFER", "the offer named no file: the stub offered the device nothing"))
    for path in sorted(api.glob("readouts__*.tar")) if api.is_dir() else []:
        shas |= {str(item.get("sha256")) for item in readout_manifest(path.read_bytes()).get("offered") or []
                 if isinstance(item, dict)}
    for sha in sorted(shas):
        held = api / f"readouts__firmware__{sha}"
        if not held.is_file():
            problems.append(("FAIL-OFFER", f"{sha[:12]}... not fetched: not held in the API's store"))
        elif hashlib.sha256(held.read_bytes()).hexdigest() != sha:
            problems.append(("FAIL-OFFER", f"{sha[:12]}... held, but not the file its SHA-256 names"))
    return problems


def readout_manifest(data: bytes) -> dict:
    """A readout.tar's manifest.json, {} where it has none to read."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            member = tar.extractfile("manifest.json")
            manifest = json.loads(member.read()) if member else {}
    except (tarfile.TarError, OSError, KeyError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def first_stage_problems(data: bytes, runs: str) -> list[str]:
    """What a readout.tar lacks of the first stage alone of a device that runs
    `runs` now (detect.NAMES' key) - the firmware eWeLink offers it, its
    memory not read - unsent from an unattended run: [] for none."""
    try:
        with tarfile.open(fileobj=io.BytesIO(data)) as tar:
            files = {member.name: tar.extractfile(member).read() for member in tar.getmembers() if member.isfile()}
    except (tarfile.TarError, OSError) as exc:
        return [f"not a tar: {exc}"]
    if list(files)[:1] != ["manifest.json"]:
        return ["manifest.json is not first"]
    problems = [] if any(name.startswith("offered/") for name in files) else ["no offered firmware"]
    problems += [f"{name} in it, from a device not on Sonoff's firmware" for name in files
                 if name != "manifest.json" and not name.startswith("offered/")]
    try:
        manifest = json.loads(files["manifest.json"])
    except ValueError:
        return problems + ["manifest.json is not JSON"]
    if manifest.get("read") is not False:
        problems.append("the manifest does not say the memory was not read")
    if manifest.get("runs") != runs:
        problems.append(f"the manifest says it runs {manifest.get('runs')}, not {runs}")
    if "contact" in manifest:
        problems.append("a contact in it, from an unattended run")
    return problems


# As tools/repo/unattended_run.py prints the screen a run ended on, in the
# output the fault suites keep of each run.
SCREEN = re.compile(r"^The screen at the end of the run \(\d+x\d+\):\n=+\n(?P<screen>.*?)^=+$", re.M | re.S)


def screen_problems(output: str) -> list[str]:
    """What is wrong with the screen each run in `output` - every recorded
    run of a scenario, as its log keeps them - ended on, as every suite
    judges a screen (unattended_run.screen_problems): [] for nothing, or no
    screen. What a run put on the clipboard is not in its log: not judged."""
    plain = re.sub(r"\x1b\[[0-9;]*m", "", output)  # printed in colour unless NO_COLOR is set
    problems: list[str] = []
    for found in SCREEN.finditer(plain):
        problems += [problem for problem in unattended_run.screen_problems(found["screen"])
                     if problem not in problems]
    return problems


# Where a results row keeps its verdict: both suites' rows since 2026-11-19;
# the Beken suite's before then kept it as OLD_VERDICT, which is still read.
VERDICT = "verdict"
OLD_VERDICT = "result"


def verdict(row: dict) -> str:
    """A results row's verdict, however old the row: HARNESS-ERROR for one
    with none."""
    return row.get(VERDICT) or row.get(OLD_VERDICT) or "HARNESS-ERROR"


def latest(results: Path) -> dict[str, dict]:
    """Each scenario's newest row in a suite's results file (JSON lines), by
    id, its verdict under VERDICT however old the row (verdict); none for a
    file not there. A line that is not a whole row - one cut short by a full
    disk before append_row took it back - is passed over, not every row after
    it lost."""
    found: dict[str, dict] = {}
    try:
        lines = results.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return found
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            found[row["id"]] = {**row, VERDICT: verdict(row)}
    return found


# One row written at a time: a row taken back must not take another's with it.
ROW_LOCK = threading.Lock()


def append_row(results: Path, row: dict) -> None:
    """`row` added to a suite's results file (JSON lines) whole or not at all:
    the line written in full and flushed to disk, or - the write failing part
    way, as on a full disk - the file cut back to how it was, so no half row is
    left for the next to run on into. A line an earlier writer left unended is
    ended first. The error is raised as it was."""
    line = (json.dumps(row) + "\n").encode("utf-8")
    results.parent.mkdir(parents=True, exist_ok=True)
    with ROW_LOCK, open(results, "a+b", buffering=0) as file:
        end = file.seek(0, os.SEEK_END)
        if end:
            file.seek(end - 1)
            if file.read(1) != b"\n":
                line = b"\n" + line
        try:
            written = 0
            while written < len(line):
                written += file.write(line[written:])
            os.fsync(file.fileno())
        except OSError:
            try:
                file.truncate(end)
            except OSError:
                pass   # latest() passes over what is left
            raise


def starved_label(verdict: str | None, notes: dict) -> str:
    """Said beside a scenario's verdict where it failed with the computer short
    of memory (memory_pressure): a failure that may be the computer's."""
    starved = verdict != "PASS" and notes.get("memory", {}).get("starved")
    return "  (memory-starved)" if starved else ""


def batch_line(instance: int, verdict: str, scenario_id: str, notes: dict, at: float | None = None) -> str:
    """What a batch prints as a scenario ends, both suites alike: the time
    (`at`, now unless given), the emulator instance, the verdict and the
    scenario, and the starved label (starved_label)."""
    return (f"{time.strftime('%T', time.localtime(at))} [{instance}] {verdict:16} {scenario_id}"
            f"{starved_label(verdict, notes)}")


def records_under(work: Path) -> Path:
    """The tool's records in an emulator's checkout `work` (its captures/):
    devices/, or plugs/ where a release before 3.0.0 keeps them - a legacy
    run's until this checkout's tool carries it on and renames them, or a
    device saved by one."""
    captures = Path(work) / "captures"
    earlier = captures / "plugs"
    return earlier if earlier.is_dir() and not (captures / "devices").is_dir() else captures / "devices"


def filed_as(log: Path) -> Path:
    """A run's file by its device and run (<mac>/runs/<when>/run.log): the
    same before and after the records' folder is renamed around it."""
    return log.relative_to(log.parents[3])


def scenario_folder(logs: Path, scenario_id: str) -> Path:
    """Where either suite keeps a scenario's logs: a folder of its own under
    `logs`, named from its id (sonoff>esphome:J2@10:crash-1 in
    sonoff-esphome_J2@10_crash-1)."""
    return logs / scenario_id.replace(":", "_").replace(">", "-")


def keep_dhcp_log(text: str, folder: Path) -> None:
    """Every address the emulator's router gave the device, and any it gave
    back (its lease), as its dnsmasq logged them: kept in `folder` as
    dhcp.log, whatever the verdict, by both families' suites alike."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dhcp.log").write_text(text, encoding="utf-8")


# A line of the router's dnsmasq log giving an address: the address, then the MAC.
DHCP_GIVEN = re.compile(r"DHCPACK\(\S+\) (\S+) (\S+)")


def dhcp_addresses(dhcp_log: str, mac: str | None = None) -> list[str]:
    """Each address the emulator's router gave the device, oldest first, from
    its dnsmasq log: by its MAC, as a readdress moves it part way; any device's
    without one (each emulator has one device)."""
    return [address for address, given_to in DHCP_GIVEN.findall(dhcp_log)
            if not mac or given_to.lower() == mac.lower()]


def device_address(leases: str, dhcp_log: str, mac: str | None = None) -> str | None:
    """The device's address now, by both families' suites alike: its newest
    lease in the router's dnsmasq lease file; or, with none - the router
    forgot it (a readdress), or the device gave it back and goes on using it,
    as ESPHome does on rejoining - the last the router gave it. None if the
    router never gave it one."""
    held = [line.split() for line in leases.splitlines()]
    held = [fields for fields in held if len(fields) > 2 and fields[0].isdigit()
            and (not mac or fields[1].lower() == mac.lower())]
    if held:
        return max(held, key=lambda fields: int(fields[0]))[2]
    given = dhcp_addresses(dhcp_log, mac)
    return given[-1] if given else None


def keep_log(source: Path, kept: Path, start: int = 0, most: int | None = None) -> None:
    """A log its emulator adds to across scenarios - the device's serial
    output, the eWeLink stub's log - kept in the scenario's folder as `kept`,
    by both families' suites alike: from `start` bytes in, where the scenario
    began (all of it where the log is shorter now, begun again), and at most
    the last `most` bytes of that. Nothing for a log not there."""
    try:
        with open(source, "rb") as log:
            size = log.seek(0, os.SEEK_END)
            begin = start if size >= start else 0
            if most is not None:
                begin = max(begin, size - most)
            log.seek(begin)
            text = log.read()
    except OSError:
        return
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_bytes(text)


def keep_runs(runs: Iterable[Path], folder: Path, before: Callable[[Path], int] = lambda path: 0) -> None:
    """Each of a scenario's run logs (`runs`, oldest first) and the events
    beside it copied into `folder` as run-1.log and run-1.events.jsonl, ...,
    whatever the verdict: the emulator's own go when its next scenario
    begins.  `before(path)`: how much of the file was there before the
    scenario began - a saved device's conversion it carries on - left out."""
    for number, log in enumerate(runs, 1):
        for source, kept in ((log, f"run-{number}.log"), (log.parent / output.EVENTS, f"run-{number}.{output.EVENTS}")):
            try:
                text = source.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue   # no events (a release before them), or filed away as it was read
            folder.mkdir(parents=True, exist_ok=True)
            (folder / kept).write_text(text[before(source):], encoding="utf-8")


def keep_evidence(records: Path, folder: Path) -> None:
    """A failed scenario's run records whole (`records`, the tool's devices/
    folder) copied into `folder`/evidence: each run's state, outcomes and
    report, and the device's own record, which say where a failure stood
    beyond what the runs' logs do.  Firmware images (the backup's .bin and
    .rbl) are left out: a megabyte or more each, and nothing a run decided."""
    if records.is_dir():
        shutil.copytree(records, folder / "evidence", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("*.bin", "*.rbl"))


# As tools/repo/unattended_run.py says where it kept a run's recording.
RECORDING = re.compile(r"^Kept in (?P<folder>\S+?)/?: screen\.ansi", re.M)


def keep_recordings(output: str, work: Path, folder: Path) -> list[Path]:
    """Each run's recording in `output` - every run of a scenario, as its log
    keeps them - copied from the emulated computer's tree `work` into `folder`,
    run-1.raw, run-2.raw...: every byte each run drew, to replay (cat it, or
    feed it to unattended_run.Screen), where the log keeps only the screen it
    ended on.  The emulator's own copies go when its next scenario begins."""
    kept = []
    for number, found in enumerate(RECORDING.finditer(output), 1):
        where = Path(found["folder"])   # relative to the tree, or /work/... inside the emulator
        source = work / (where.relative_to("/work") if where.is_absolute() else where) / "screen.raw"
        if source.is_file():
            folder.mkdir(parents=True, exist_ok=True)
            kept.append(Path(shutil.copy(source, folder / f"run-{number}.raw")))
    return kept
