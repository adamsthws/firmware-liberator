#!/usr/bin/env python3
"""Attended runs in the emulators: the tool run as a person sits through it,
every question asked and answered as a scenario's answer set says
(tools/repo/unattended_run.py --attended), and the screen judged at every
answer and at the end - what the fault suites cannot see, as they run the
tool unattended.

A scenario is a JSON file in tools/espressif/emulator/attended/ (an S60's,
in the Espressif emulator) or tools/beken/emulator/attended/ (an S61's):

    {"about": "what it shows", "covers": [situation, ...],
     "start": "fresh" | a saved device (unattended-test.sh --from; on an S61,
              one the Beken fault suite's prep saved, folders.SAVED),
     "prep": [...], "args": [...], "size": "100x40", "minutes": 30,
     "answers": [{"when": regex, "type": keys, "times": n, "optional": bool,
                  "shows": [regex, ...], "never": [regex, ...]}, ...],
     "expect": {"exit": 0, "result": "Converted", "screen": true, "shows": [regex, ...],
                "never": [regex, ...]},
     "again": {"answers": [...], "args": [...], "expect": {...}}}

`covers`: the situations it tries, each to be tried in every cell - a
conversion and a readout, in each chip family - or NOT_EVERYWHERE says why
not (coverage_gaps). Every key is taken by both families' runners alike, but
those ONE_FAMILY_KEYS names, with why.

`answers`: the scenario's own, before SHARED_ANSWERS - what every run is
answered alike - for each question it does not answer itself (with_shared).

`again`: the tool run once more on the device as the first run left it, with
no --new - a run stopped part way, offered back ("did not finish") - judged
as the first is.

`prep`, before the run, on the device (Espressif only): ["convert", FIRMWARE]
first, if at all (the device converted to it, unattended); "sudo forgets" (sudo
remembers no password in the emulator, so it asks again at every use - the
end of the run too); ["tasmota password", PASSWORD] (Tasmota's web password
set); ["locked esphome", "login" | "login and updates"] (this project's
ESPHome with a web login, admin and "test-login-1", and the made-up API
encryption key LOCKED_KEY; and with "updates", taking only encrypted
updates). An answer is typed at its question only (unattended_run.Answer);
one `optional` need not be asked. An answer's `shows`: lines on the screen
each time it is typed, for what leaves the screen before the run ends - why
the answer before it was not taken, say; its `never`: lines not on it then,
for what an earlier answer should have taken off. In place of `type`, an
answer can switch the device ("power": "off") or its home Wi-Fi's router
("router": "off", or on the Beken emulator "on NEW-PASSWORD", the password
changed), move the computer onto the device's own network or back ("wifi":
"join", "home"), or signal the run ("signal": "TERM").
`command`: "readout" for a readout, not a conversion; `api_enough`: true
for the API beside the emulator to have enough readouts of the device's
product already (AUTOFLASH_API_ENOUGH), so a readout of it is not needed;
`api_down`: true for the readout to go where nothing answers (API_DOWN), so
it cannot be sent and is kept; `api_links`: true for that API to take
offers' links in place of their files (AUTOFLASH_LOCAL_OFFERED_SEND=links),
so the offer the run sends beside it goes to /v1/offers, its file fetched
from the stub on first sight, before the person's own readout of the device;
`account`: one of stub_cloud.accounts' made-up accounts, its other devices
listed by the stub beside the emulated one, for a readout of every device
(args ["--all"]) or of one chosen at the menu of them (reads_every_device).

The verdict, from the recording alone (judge):
    PASS              ended as expected, every answer typed, nothing wrong
    FAIL-RESULT       ended otherwise: its exit code, or its report's Result
                      (the recorder's result.txt, from the run's log)
    FAIL-UNANSWERED   it ended, or its --timeout stopped it, at a question no
                      answer covers: new, or reworded
    FAIL-UNUSED       an answer never typed: its question no longer asked
    FAIL-ANSWER       an answer that switches the device or the router, or
                      moves the computer's Wi-Fi, failed to: the network not on
                      the air to join, say
    FAIL-QUESTION     as a question was answered: no "(q to quit)" on it, a
                      row the terminal wrapped itself, or a question shown
                      twice - an earlier try left on the screen
    FAIL-SCREEN       the screen it ended on, as every suite judges one
                      (unattended_run.screen_problems): its stages, a row said
                      twice, its report and how it ended (with what the
                      run put on the clipboard), a row the terminal wrapped
                      itself, or a line `expect`'s `shows` wants not on it, or
                      one its `never` wants nowhere: on the screen at any
                      answer, or at the end - a key pressed by accident,
                      echoed; or, as an answer was typed, a line its `shows`
                      wants not on the screen, or one its `never` does not
                      want on it
    FAIL-NOT-ASKED, FAIL-NOT-SENT, FAIL-SENT-UNWANTED, FAIL-NOT-STORED,
    FAIL-READOUT, FAIL-KEPT-UNSENT
                      a readout's other side: what the API beside the
                      emulator was asked and kept (readout_verdicts,
                      fault_verdicts.readout_api_problems); of an `api_down`
                      scenario's, FAIL-READOUT where the readout kept is not
                      whole, or none was kept
    FAIL-OFFER, FAIL-NOT-WANTED
                      of an `api_links` scenario's, the offer sent beside the
                      run never sent or taken, not in the API's store, or its
                      files not fetched and held; or the readout, after that
                      offer, not wanted (fault_verdicts.own_offer_problems)
    NOT-FIRED         of an `api_links` scenario's, the offer sent only after
                      the readout asked /v1/wanted: the case not tried, so
                      neither a pass nor a failure, as a fault suite's fault
                      that never happened; it wins over any other verdict

    tools/espressif/emulator/attended_suite.py list
    tools/espressif/emulator/attended_suite.py run [NAME ...] [--workers N] [--first-instance N] --device MAC

Its results, and each run's recording, go to captures/emulation/attended/<time>/.
Run on a computer with the emulators (docs/emulator.md); each scenario runs
on an emulator of its own, --workers at once - the S61's after, one at a
time - numbered from --first-instance (61) to one per worker, each started
afresh: a run uses no more instance numbers than it has workers, and refuses
to start on one already running (tools/repo/emulator_instances.py).
"""

from __future__ import annotations

import argparse
import base64
import codecs
import concurrent.futures
import datetime
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from tools.autoflash import kinds, readout, records  # noqa: E402
from tools.beken.emulator import folders  # noqa: E402
from tools.ewelink.stub_cloud import accounts  # noqa: E402
from tools.repo import emulator_instances, emulator_stats, fault_verdicts, unattended_run  # noqa: E402

SCENARIOS = {"espressif": ROOT / "tools/espressif/emulator/attended",
             "beken": ROOT / "tools/beken/emulator/attended"}
RESULTS = ROOT / "captures/emulation/attended"
FIRST_INSTANCE = 61
SIZE = (100, 40)
# The made-up key of the locked ESPHome builds (build_locked).
LOCKED_KEY = base64.b64encode(bytes(range(32))).decode()
LOCKED_LOGIN = ("admin", "test-login-1")
# The tool quit at its first question, for prep: and the email question it asks
# as it ends answered, or the run waits there until its --timeout.
QUIT_FIRST = [{"when": "Which device is this run for|Press any key", "type": "q\r"},
              {"when": "\\[[Yy]/n\\]", "type": "n\r"}]
DEVICE = "192.168.60.50"
# Where an `api_down` scenario's readout goes: the discard port on the tool's
# own computer, where nothing listens, so it is refused at once.
API_DOWN = "http://127.0.0.1:9/v1/readout"


# What a scenario may say (the docstring above). Each is taken by both chip
# families' runners alike - run_espressif and run_again, run_beken - through
# run_environment and api_options, but those named here, the family that
# alone takes it, and why (tests/espressif/emulator/test_attended_suite.py
# fails on a scenario of the other family that says it).
KEYS = ("about", "covers", "start", "prep", "args", "size", "minutes", "answers", "expect", "command", "again",
        "api_enough", "api_down", "api_links", "account")
ONE_FAMILY_KEYS = {
    "prep": ("espressif", "what it does to the device first - a conversion, a Tasmota web password, a locked "
                          "ESPHome, sudo remembering nothing - is done in the S60's emulator; an S61 starts from "
                          "a device the Beken fault suite's prep saved instead (`start`)"),
}

# Each scenario names the situations it covers (`covers`). Every situation one
# covers is to be covered in every cell - a conversion and a readout, in each
# chip family - so the families, and conversions and readouts, are tried
# alike and cannot drift apart: coverage_gaps() lists any cell without it,
# and tests/espressif/emulator/test_attended_suite.py fails on one. A
# situation that cannot be in every cell is named here, with the cells it is
# for - "conversion", "readout", "espressif", "beken", each narrowing - and why.
NOT_EVERYWHERE: dict[str, tuple[tuple[str, ...], str]] = {
    "accepted": (("readout",), "what the website made of a readout sent to it: a conversion sends none"),
    "not-needed": (("readout",), "the website holding enough of a readout's product: a conversion asks it nothing"),
    "not-sent": (("readout",), "a readout the website could not be sent, kept for later: a conversion sends none"),
    "update": (("conversion",), "a readout leaves the firmware it finds: there is nothing to update"),
    "back-to-sonoff": (("conversion",), "a readout puts Sonoff's firmware back itself, as a stage of its own"),
    "back-to-sonoff-put-back": (("conversion", "espressif"),
                                "--simulate-no-identity and --simulate-no-sonoff-firmware act on the S60's route "
                                "alone; an S61 goes back to Sonoff from the image eWeLink offers, never from a "
                                "backup (routes/beken SONOFF_FROM_BACKUP)"),
    "image-refused": (("conversion",), "--simulate-bad-image spoils an image a conversion sends; a readout's "
                                       "reading program is none of them"),
    "kept-as-it-is": (("conversion", "espressif"), "only Tasmota is kept as it is (its meter checked) - any other "
                                                   "firmware a device runs is left out of the choices - and only "
                                                   "an S60 runs Tasmota"),
    "device-password": (("conversion", "espressif"), "a Tasmota web password or a locked ESPHome is set first by "
                                                     "prep, in the S60's emulator alone; OpenBeken has no web "
                                                     "password, and a readout of a converted device sends it "
                                                     "nothing"),
    "sudo-asked-again": (("espressif",), "the S61's emulated computer has no sudo (beken-emu.Dockerfile), so it "
                                         "is never asked for"),
    "wifi-typed": (("conversion", "espressif"), "an S61's Wi-Fi details are typed whether --wifi is given or not "
                                                "(context.py), and a readout sends a device none"),
    "home-wifi-changed": (("conversion", "beken"), "only the Beken emulator's router can change the home Wi-Fi's "
                                                   "password (beken-emu.sh router on PASSWORD), and a readout "
                                                   "leaves the device on the network it found it on"),
    "own-network": (("conversion", "beken"), "ESPHome raises its own network when the home Wi-Fi's password has "
                                             "changed, which only the Beken emulator's router can do"),
    "own-network-joined-early": (("conversion", "beken"), "as own-network"),
    "chosen-from-the-account": (("readout",), "a readout offers the account's devices to choose from "
                                              "(readout.choose); a conversion offers those found on the network "
                                              "(routes/source.py), asked in every scenario of a device on another "
                                              "firmware"),
    "every-device": (("readout",), "a conversion is of one device; a readout alone goes through every device in "
                                   "the account (readout --all)"),
    "own-offer-first": (("readout",), "a readout of the device after the offer the run sent of it beside it: a "
                                      "conversion sends that offer too, but no readout after it"),
    "address-typed": (("conversion",), "a readout asks the address only of a device that answers nowhere on the "
                                       "network, which neither emulator can arrange while eWeLink lists it online"),
}


def cell(family: str, scenario: dict[str, Any]) -> tuple[str, str]:
    """Where a scenario is in the coverage matrix: what it runs, in which chip family."""
    return ("readout" if scenario.get("command") == "readout" else "conversion", family)


def coverage_gaps(found: dict[str, tuple[str, dict[str, Any]]]) -> list[str]:
    """Each situation the scenarios cover that a cell it is for has no
    scenario of (NOT_EVERYWHERE): [] for none."""
    covered: dict[tuple[str, str], set[str]] = {}
    for family, scenario in found.values():
        covered.setdefault(cell(family, scenario), set()).update(scenario.get("covers", []))
    cells = [(command, family) for command in ("conversion", "readout") for family in SCENARIOS]
    gaps = []
    for situation in sorted(set().union(*covered.values()) if covered else set()):
        within = NOT_EVERYWHERE.get(situation, ((), ""))[0]
        for where in cells:
            if all(narrowing in where for narrowing in within) and situation not in covered.get(where, set()):
                gaps.append(f"{situation}: no {where[0]} scenario of {where[1]} covers it")
    return gaps


# The readout's question when the account holds several devices: its first
# option is every device.
CHOOSE_READOUT = readout.CHOOSE


# What a person answers in every run alike, of either family, a conversion or
# a readout: the device chosen, the computer's password, the go-ahead word,
# the sign-in, the questions as it ends. Each scenario has them after its own
# answers (with_shared) for every question it does not answer itself by the
# same `when`: one it does is answered its way alone - its count, its checks,
# a signal in place of typing - and the shared one left out. All optional: a
# run need not ask them. A question reworded is changed here, once.
SHARED_ANSWERS: tuple[dict[str, Any], ...] = (
    {"when": "Which device is this run for", "type": "1\r", "optional": True},
    {"when": CHOOSE_READOUT, "type": "1\r", "optional": True},
    {"when": "This computer's password \\(for sudo\\)", "type": "autoflash\r", "times": 6, "optional": True},
    {"when": "Press any key", "type": "x", "times": 3, "optional": True},
    {"when": "Type .+ to go ahead", "type": "PROCEED\r", "times": 3, "optional": True},
    {"when": "Which eWeLink account is this device in", "type": "2\r", "optional": True},
    {"when": "eWeLink account email or phone number", "type": "someone@example.invalid\r", "optional": True},
    {"when": "eWeLink password for", "type": "made-up-password\r", "optional": True},
    {"when": "What next", "type": "q\r", "optional": True},
    {"when": "\\[[Yy]/n\\]", "type": "n\r", "optional": True},
    {"when": "\\(y/N[^)]*\\)|\\(Y/n[^)]*\\)", "type": "y\r", "times": 3, "optional": True},
)


def with_shared(answers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A run's answers: its own, then the shared ones for the questions it
    does not answer itself (SHARED_ANSWERS)."""
    own = {answer["when"] for answer in answers}
    return [*answers, *(dict(answer) for answer in SHARED_ANSWERS if answer["when"] not in own)]


def scenarios() -> dict[str, tuple[str, dict[str, Any]]]:
    """Every scenario, by name: its family's, and what its file says, each
    run's answers with the shared ones (with_shared)."""
    found = {}
    for family, folder in SCENARIOS.items():
        for path in sorted(folder.glob("*.json")):
            scenario = json.loads(path.read_text(encoding="utf-8"))
            for run in (scenario, *([scenario["again"]] if "again" in scenario else [])):
                run["answers"] = with_shared(run.get("answers", []))
            found[path.stem] = (family, scenario)
    return found


# ---------------------------------------------------------------- judging


def screen_at(raw: bytes, upto: int, size: tuple[int, int] = SIZE) -> unattended_run.Screen:
    screen = unattended_run.Screen(*size)
    screen.feed(codecs.decode(raw[:upto], "utf-8", errors="replace"))
    return screen


def question_problems(screen: unattended_run.Screen, wrapped_before: int) -> list[str]:
    """What is wrong with the screen as a question was answered."""
    problems = []
    rows = [row for row in screen.render(colour=False).splitlines() if row.strip()]
    asked = unattended_run.question(screen)
    if "(q to quit)" not in asked:
        problems.append(f"no (q to quit) at {asked.strip().splitlines()[-1].strip()[:60]!r}")
    if screen.wrapped > wrapped_before:
        problems.append("a row the terminal wrapped itself")
    since = max((index for index, row in enumerate(rows) if unattended_run.STAGE_ROW.match(row)), default=-1)
    shown = [row.split("(q to quit)")[0].strip() for row in rows[since + 1:] if "(q to quit)" in row]
    twice = sorted({question for question in shown if shown.count(question) > 1})
    if twice:
        problems.append(f"shown twice: {twice[0][:60]!r}")
    return problems


def judge(scenario: dict[str, Any], raw: bytes, answers: list[dict[str, Any]], code: int,
          size: tuple[int, int] = SIZE, result: str | None = None, clipboard: str | None = None,
          stopped_at: int | None = None) -> list[str]:
    """The verdicts of a run, from its recording (screen.raw), the answers as
    the recorder kept them (answers.json, with typed_at), its exit code, its
    report's Result (result.txt), what it put on the clipboard
    (clipboard.txt; "" for nothing, None where that is not known) and, for
    a run its --timeout stopped, how much it had written then
    (stopped-at.txt): empty for a PASS."""
    expect = scenario.get("expect", {})
    verdicts = []
    final = screen_at(raw, len(raw), size)
    text = final.render(colour=False)
    # A run stopped by its timeout takes the question it waited at off the
    # screen as it stops: seen, only "exit 143" said why one hung 20 minutes.
    asked = unattended_run.question(final if stopped_at is None else screen_at(raw, stopped_at, size))
    if unattended_run.waiting(asked) and "(q to quit)" in asked:
        waiting = next(row for row in reversed(asked.splitlines()) if "(q to quit)" in row)
        verdicts.append(f"FAIL-UNANSWERED: {waiting.strip()[:70]!r}")
    if code != expect.get("exit", 0):
        verdicts.append(f"FAIL-RESULT: exit {code}, not {expect.get('exit', 0)}")
    if expect.get("result") and not (result or "").startswith(expect["result"]):
        verdicts.append(f"FAIL-RESULT: Result {result or 'none'!r}, not {expect['result']!r}")
    # The recorder keeps the answers it was given, in their order: paired by
    # place, as two can say the same (a password typed again, each judged apart).
    for answer, item in zip(answers, scenario["answers"], strict=True):
        if not answer["typed_at"] and not item.get("optional"):
            verdicts.append(f"FAIL-UNUSED: {answer['when']!r}")
        for why in answer.get("failed", []):
            verdicts.append(f"FAIL-ANSWER: {answer['when']!r}: {why}")
        for at in answer["typed_at"] if item.get("shows") or item.get("never") else []:
            shown = screen_at(raw, at, size).render(colour=False)
            verdicts += [f"FAIL-SCREEN: not shown at {answer['when']!r}: {wanted!r}"
                         for wanted in item.get("shows", []) if not re.search(wanted, shown, re.M)]
            verdicts += [f"FAIL-SCREEN: shown at {answer['when']!r}: {unwanted!r}"
                         for unwanted in item.get("never", []) if re.search(unwanted, shown, re.M)]
    wrapped = 0
    # A key typed anywhere (a Ctrl-C as a stage shows) answers no question.
    for at in sorted(at for answer in answers if not answer.get("anywhere") for at in answer["typed_at"]):
        screen = screen_at(raw, at, size)
        for problem in question_problems(screen, wrapped):
            verdicts.append(f"FAIL-QUESTION: {problem}")
        wrapped = screen.wrapped
    if expect.get("screen", True):
        verdicts += [f"FAIL-SCREEN: {problem}" for problem in unattended_run.screen_problems(text, clipboard, size[1])]
        if final.wrapped > wrapped:
            verdicts.append("FAIL-SCREEN: a row the terminal wrapped itself, after the last answer")
    for unwanted in expect.get("never", []):
        seen = [screen_at(raw, at, size).render(colour=False) for answer in answers for at in answer["typed_at"]]
        if any(re.search(unwanted, shown, re.M) for shown in [*seen, text]):
            verdicts.append(f"FAIL-SCREEN: shown: {unwanted!r}")
    for wanted in expect.get("shows", []):
        if not re.search(wanted, text, re.M):
            verdicts.append(f"FAIL-SCREEN: not shown: {wanted!r}")
    return verdicts


# ---------------------------------------------------------------- running


def run_answers(scenario: dict[str, Any], folder: Path, name: str = "answers-given.json") -> Path:
    """The scenario's answers as the recorder takes them (no `optional`,
    `shows` or `never`: they are for judge)."""
    path = folder / name
    path.write_text(json.dumps([{key: value for key, value in item.items()
                                 if key not in ("optional", "shows", "never")}
                                for item in scenario["answers"]], indent=1), encoding="utf-8")
    return path


def docker(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def in_container(n: int, code: str) -> str:
    """Python in emulator `n`'s container, as the tool runs there."""
    done = docker("exec", "-u", f"{os.getuid()}:{os.getgid()}", "-w", "/work", f"autoflash-emu-{n}",
                  "/opt/venv/bin/python", "-c", code)
    return (done.stdout + done.stderr).strip()


def sudo_forgets(n: int, stop: threading.Event) -> None:
    """sudo remembering no password in emulator `n`, as soon as it is up."""
    while not stop.is_set():
        if docker("exec", "-u", "root", f"autoflash-emu-{n}", "true").returncode == 0:
            docker("exec", "-u", "root", f"autoflash-emu-{n}", "sh", "-c",
                   'echo "Defaults timestamp_timeout=0" > /etc/sudoers.d/zz-no-cache'
                   " && chmod 440 /etc/sudoers.d/zz-no-cache")
            return
        time.sleep(0.5)


def build_locked(variant: str) -> Path:
    """This project's S60 ESPHome with a web login and the made-up API key -
    taking only encrypted updates too, for "login and updates" - built as the
    published one is, with the emulator's Wi-Fi written in. Kept in
    captures/emulation/attended/ for the next run."""
    from tools.autoflash import imprint
    from tools.repo import update_firmware
    config = (ROOT / "devices/sonoff-s60/esphome.yaml").read_text(encoding="utf-8")
    release = re.search(r'version: "(.+?)"', config).group(1)
    built = RESULTS / f"locked-{variant.replace(' ', '-')}-{release}.ota.bin"
    if built.exists():
        return built
    config = config.replace("\napi:\n", f'\napi:\n  encryption:\n    key: "{LOCKED_KEY}"\n', 1)
    config = config.replace("web_server:\n  version: 3\n", "web_server:\n  version: 3\n  auth:\n"
                            f"    username: {LOCKED_LOGIN[0]}\n    password: {LOCKED_LOGIN[1]}\n", 1)
    if "updates" in variant:
        config = config.replace("ota:\n  - platform: esphome\n", "ota:\n  - platform: esphome\n    encryption: {}\n", 1)
    pins = json.loads((ROOT / "artifacts/firmware.json").read_text(encoding="utf-8"))
    image = next(entry["builder"] for entry in _entries(pins)
                 if entry.get("config") == "devices/sonoff-s60/esphome.yaml" and entry.get("builder"))
    # On the disk, not in /tmp - its ESP-IDF is gigabytes, and a small /tmp
    # filled up - and kept, so the next build has it.
    work = RESULTS / "esphome-build"
    work.mkdir(parents=True, exist_ok=True)
    path = work / "esphome.yaml"
    path.write_text(config, encoding="utf-8")
    app, _ = update_firmware.build(image.split("@")[0], path, release, work)
    built.parent.mkdir(parents=True, exist_ok=True)
    built.write_bytes(imprint.imprint(app, "PICSimLabWifi", ""))
    return built


def _entries(node: Any):
    """Every dict in firmware.json, however deep."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _entries(value)
    elif isinstance(node, list):
        for value in node:
            yield from _entries(value)


def product_of(family: str, mac: str | None) -> str:
    """The emulated device's product model, as eWeLink lists it: the S60
    copy's record's (as fault_suite.device_product() gives it), or the S61's."""
    if family == "beken":
        return kinds.SONOFF_S61.example_product_model
    return ((records.read_device(mac) if mac else {}).get("product_model")
            or kinds.SONOFF_S60.example_product_model).upper()


def run_environment(scenario: dict[str, Any], family: str, mac: str | None) -> dict[str, str]:
    """The environment a scenario's run goes on in, in either chip family -
    both runners take it from here, so a setting cannot reach one alone: the
    API beside the emulator told it has enough of the device's product where
    the scenario says (`api_enough`), as fault_suite.enough, and taking
    offers' links for `api_links`; its readout sent
    where nothing answers for `api_down`; and on the S60's stub the device
    listed under its product. Without that, the stub takes the product from
    this checkout's earlier runs (emu.sh), and on one with none lists no
    product: a readout then sent no model, which the API refused (400)."""
    environment = {name: value for name, value in os.environ.items() if name != "AUTOFLASH_EMU_STUB_OTHERS"}
    product = product_of(family, mac)
    environment.setdefault("AUTOFLASH_EMU_STUB_PRODUCT_MODEL", product)
    if scenario.get("api_enough"):
        environment["AUTOFLASH_API_ENOUGH"] = product
    # How the API beside it takes offers: as the scenario says, never as this
    # computer's environment happens to (local.mjs takes files unset).
    environment["AUTOFLASH_LOCAL_OFFERED_SEND"] = "links" if scenario.get("api_links") else "files"
    if scenario.get("account"):
        # The account's other devices, made up, for the stub to list beside the
        # device (emu.sh, beken-emu.sh device): one of stub_cloud.accounts'.
        listed = RESULTS / "accounts" / f"{scenario['account']}.json"
        listed.parent.mkdir(parents=True, exist_ok=True)
        listed.write_text(json.dumps(accounts.ACCOUNTS[scenario["account"]], indent=1) + "\n", encoding="utf-8")
        environment["AUTOFLASH_EMU_STUB_OTHERS"] = str(listed)
    return environment | api_environment(scenario)


def api_environment(scenario: dict[str, Any]) -> dict[str, str]:
    """An `api_down` scenario's readout sent to API_DOWN, in either family."""
    return {"AUTOFLASH_READOUT_URL": API_DOWN} if scenario.get("api_down") else {}


def api_options(scenario: dict[str, Any]) -> list[str]:
    """No API beside the emulator for an `api_down` scenario: its readout
    goes to API_DOWN instead (api_environment)."""
    return ["--no-api"] if scenario.get("api_down") else []


def wrapper(n: int, scenario: dict[str, Any], *more: str, mac: str) -> list[str]:
    readout = ["--readout"] if scenario.get("command") == "readout" else []
    return [str(ROOT / "tools/espressif/emulator/unattended-test.sh"), f"--instance={n}", f"--device={mac}",
            "--stub-cloud", *readout, *api_options(scenario), *more]


def prepare(n: int, scenario: dict[str, Any], folder: Path, log, mac: str) -> bool:
    """The device as the scenario starts it, before its run: from where it says,
    then the tool quit at its first question with the emulator kept, for
    what `prep` does to the device. False when that could not be done."""
    start = scenario.get("start", "fresh")
    actions = [action for action in scenario.get("prep", []) if action != "sudo forgets"]
    first = ["--fresh"] if start == "fresh" else [f"--from={start}"]
    if not actions:
        return True   # its run starts it, from where it says
    if actions[0][0] == "convert":
        # Converted first, unattended: a device saved on one firmware, wanted on another.
        code = subprocess.call(wrapper(n, {}, *first, "--timeout=40", "--", "--new", "--firmware",
                                       actions[0][1], mac=mac), stdout=log, stderr=subprocess.STDOUT)
        log.write(f"PREP converted to {actions[0][1]}: exit {code}\n")
        docker("stop", f"autoflash-emu-{n}")
        actions, first = actions[1:], []
        if code != 0:
            return False
        if not actions:
            return True
    quit_first = folder / "quit-first.json"
    quit_first.write_text(json.dumps(QUIT_FIRST), encoding="utf-8")
    subprocess.call(wrapper(n, {}, *first, "--keep", "--timeout=8", f"--answers={quit_first}",
                            "--", "--new", mac=mac), stdout=log, stderr=subprocess.STDOUT)
    for action in actions:
        kind, value = action
        if kind == "tasmota password":
            for _ in range(12):   # the device may still be starting
                # As the tool sends Tasmota a command: a plain request was hung up on.
                said = in_container(n, "from tools.autoflash.device import Device; "
                                    f"print(Device('{DEVICE}').command('Backlog WebPassword {value}; Restart 1', "
                                    "timeout=20))")
                log.write(f"PREP tasmota password: {said}\n")
                if "Traceback" not in said:   # Backlog answers {}
                    break
                time.sleep(10)
            else:
                return False
            time.sleep(30)   # restarted: its settings saved
        elif kind == "locked esphome":
            image = build_locked(value)
            target = ROOT / f"captures/emulation/state-{n}/work/locked.ota.bin"
            shutil.copyfile(image, target)
            said = in_container(n, f"from tools.autoflash import esphome_ota; esphome_ota.upload('{DEVICE}', "
                                "open('locked.ota.bin', 'rb').read()); print('sent')")
            target.unlink(missing_ok=True)
            log.write(f"PREP locked esphome ({value}): {said}\n")
            # A minute on its new release, or a power cut takes the device back to the one it had.
            time.sleep(90)
            check = in_container(n, "import urllib.request, urllib.error\ntry:\n urllib.request.urlopen("
                                 f"'http://{DEVICE}/', timeout=5); print(200)\n"
                                 "except urllib.error.HTTPError as e: print(e.code)\nexcept Exception: print('down')")
            log.write(f"PREP its page answers: {check}\n")
            if check != "401":
                return False
        else:
            raise ValueError(f"no such prep: {kind}")
    log.flush()
    docker("stop", f"autoflash-emu-{n}")
    return True


def run_espressif(n: int, name: str, scenario: dict[str, Any], folder: Path, mac: str) -> tuple[int, Path | None]:
    """The scenario on emulator `n`: its exit code, and where its recording is."""
    with open(folder / "harness.log", "a", encoding="utf-8") as log:
        if not prepare(n, scenario, folder, log, mac):
            docker("stop", f"autoflash-emu-{n}")   # kept for what prep was to do
            return 125, None
        start = scenario.get("start", "fresh")
        first = [] if scenario.get("prep") and any(a != "sudo forgets" for a in scenario["prep"]) else (
            ["--fresh"] if start == "fresh" else [f"--from={start}"])
        stop = threading.Event()
        if "sudo forgets" in scenario.get("prep", []):
            threading.Thread(target=sudo_forgets, args=(n, stop), daemon=True).start()
        command = wrapper(n, scenario, *first, f"--timeout={scenario.get('minutes', 30)}",
                          f"--size={scenario.get('size', '100x40')}",
                          f"--answers={run_answers(scenario, folder)}", "--", "--new", *scenario.get("args", []),
                          mac=mac)
        log.write("--- " + " ".join(command[1:]) + "\n")
        log.flush()
        out = subprocess.run(command, capture_output=True, text=True, env=run_environment(scenario, "espressif", mac))
        stop.set()
        log.write(out.stdout + out.stderr)
        docker("stop", f"autoflash-emu-{n}")
    found = re.search(r"Exit code (-?\d+)", out.stdout)
    where = re.search(r"On this computer: (\S+)", out.stdout)
    return (int(found.group(1)) if found else out.returncode), (ROOT / where.group(1) if where else None)


def run_again(n: int, scenario: dict[str, Any], folder: Path, mac: str) -> tuple[int, Path | None]:
    """The tool once more on emulator `n`'s device as the first run left it:
    no --new, so a run left unfinished is offered back."""
    again = scenario["again"]
    with open(folder / "harness.log", "a", encoding="utf-8") as log:
        command = wrapper(n, {"command": scenario.get("command")} | again, f"--timeout={again.get('minutes', 30)}",
                          f"--size={scenario.get('size', '100x40')}",
                          f"--answers={run_answers(again, folder, 'again-answers-given.json')}", "--",
                          *again.get("args", []), mac=mac)
        log.write("--- " + " ".join(command[1:]) + "\n")
        log.flush()
        out = subprocess.run(command, capture_output=True, text=True,
                             env=run_environment(scenario | again, "espressif", mac))
        log.write(out.stdout + out.stderr)
        docker("stop", f"autoflash-emu-{n}")
    found = re.search(r"Exit code (-?\d+)", out.stdout)
    where = re.search(r"On this computer: (\S+)", out.stdout)
    return (int(found.group(1)) if found else out.returncode), (ROOT / where.group(1) if where else None)


BEKEN_EMU = ROOT / "tools/beken/emulator/beken-emu.sh"


def beken_ready(n: int, state: Path, wait: float = 300) -> bool:
    """Whether Beken emulator `n`'s device, started from a saved state or as
    the last run left it, is up: its stub serving (its CA made, which the
    computer needs) and the device on its Wi-Fi (a lease from its router), as
    the Beken fault suite waits for one (Instance.ready). beken-emu.sh device
    returns at once for one whose saved stub record already says it is in
    the account, before either."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        leased = docker("exec", f"autoflash-beken-emu-{n}", "sh", "-c",
                        "sort -n /run/autoflash-beken-emu/leases 2>/dev/null | tail -1").stdout.strip()
        if (state / "ca.crt").exists() and leased:
            return True
        time.sleep(3)
    return False


def run_beken(n: int, name: str, scenario: dict[str, Any], folder: Path, mac: str) -> tuple[int, Path | None]:
    """The scenario on Beken emulator `n`: the S61 as it starts - fresh from
    the dump, or a device the Beken fault suite's prep saved (`start`) - then
    the tool on the computer on its Wi-Fi (beken-emu.sh computer), its
    eWeLink sign-in typed. The device is left running for a scenario's
    `again` (run_again_beken)."""
    state, start = state_of("beken", n), scenario.get("start", "fresh")
    # The device's own start takes the scenario's settings too: its stub lists an
    # `account`'s other devices (beken-emu.sh device).
    environment = run_environment(scenario, "beken", mac)
    with open(folder / "harness.log", "a", encoding="utf-8") as log:
        if start == "fresh":
            subprocess.call([str(BEKEN_EMU), "--instance", str(n), "--fresh", "device"], stdout=log,
                            stderr=subprocess.STDOUT, timeout=1200, env=environment)
        else:
            stop_beken(n)
            folders.clear(state)
            shutil.copytree(folders.SAVED / start, state)
            subprocess.call([str(BEKEN_EMU), "--instance", str(n), "device"], stdout=log,
                            stderr=subprocess.STDOUT, timeout=1200, env=environment)
            if not beken_ready(n, state):
                log.write("HARNESS: the device did not come up on its network within 5 minutes\n")
        result = beken_computer(n, scenario, run_answers(scenario, folder), "attended", ["--new"], log,
                                environment)
        if not scenario.get("again"):
            stop_beken(n)
    return result


def run_again_beken(n: int, scenario: dict[str, Any], folder: Path, mac: str) -> tuple[int, Path | None]:
    """The tool once more on Beken emulator `n`'s device as the first run left
    it, as run_again does on an S60's: no --new."""
    again = scenario["again"]
    environment = run_environment(scenario | again, "beken", mac)
    if again.get("api_enough") != scenario.get("api_enough"):
        # The API beside the device goes on between runs, as it was started:
        # started again with what it has enough of, its store kept.
        docker("rm", "-f", f"autoflash-beken-emu-{n}-api")
    with open(folder / "harness.log", "a", encoding="utf-8") as log:
        try:
            return beken_computer(n, {"command": scenario.get("command"), "size": scenario.get("size")} | again,
                                  run_answers(again, folder, "again-answers-given.json"), "attended-again", [], log,
                                  environment)
        finally:
            stop_beken(n)


def beken_computer(n: int, scenario: dict[str, Any], answers: Path, save: str, first: list[str],
                   log: Any, environment: dict[str, str]) -> tuple[int, Path]:
    """The tool run attended on the computer on Beken emulator `n`'s Wi-Fi:
    its exit code, and its recording, kept under captures/unattended/`save`."""
    state = state_of("beken", n)
    (state / "work/captures").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(answers, state / f"work/captures/{answers.name}")
    command = [str(BEKEN_EMU), "--instance", str(n), *api_options(scenario), "computer", "tools/repo/unattended_run.py",
               "--size", scenario.get("size") or "100x40", "--timeout", str(scenario.get("minutes", 60)),
               "--attended", "--answers", f"captures/{answers.name}", "--save", f"captures/unattended/{save}",
               *(["--command", "readout"] if scenario.get("command") == "readout" else []),
               "--", *first, *scenario.get("args", [])]
    log.write("--- " + " ".join(command[1:]) + "\n")
    log.flush()
    out = subprocess.run(command, capture_output=True, text=True, env=environment)
    log.write(out.stdout + out.stderr)
    found = re.search(r"Exit code (-?\d+)", out.stdout)
    return (int(found.group(1)) if found else out.returncode), state / f"work/captures/unattended/{save}"


def stop_beken(n: int) -> None:
    """Beken emulator `n` stopped: its device, and any computer or API beside it."""
    for container in docker("ps", "--format", "{{.Names}}").stdout.split():
        if container == f"autoflash-beken-emu-{n}" or container.startswith(f"autoflash-beken-emu-{n}-"):
            docker("stop", "-t", "30", container)


def state_of(family: str, n: int) -> Path:
    """Emulator `n`'s state: the tool's records under work/captures/, and the
    API beside it in api/ (unattended-test.sh, beken-emu.sh computer)."""
    return ROOT / ("captures/emulation/beken" if family == "beken" else "captures/emulation") / f"state-{n}"


def firmware_at_start(scenario: dict[str, Any]) -> str | None:
    """The firmware other than Sonoff's the device runs as the scenario's run
    starts (detect.NAMES' key) - converted by `prep`, or a saved device on it
    (`start`) - whose readout is its first stage alone; None for Sonoff's."""
    converted = [action[1] for action in scenario.get("prep", []) if isinstance(action, list) and action[0] == "convert"]
    named = converted[-1] if converted else scenario.get("start", "fresh")
    return next((firmware for firmware in ("tasmota", "esphome", "openbeken") if firmware in named), None)


def requests_made(state: Path) -> int:
    """How long the API's requests.log beside emulator `state` is: what a run
    adds after it is that run's (readout_verdicts)."""
    requests = state / "api/requests.log"
    return requests.stat().st_size if requests.exists() else 0


def reads_every_device(scenario: dict[str, Any]) -> bool:
    """Whether a readout scenario reads out every device in its `account`:
    asked so with --all, or by the last answer to the device question
    choosing its first option - else it reads out the one device chosen."""
    if not scenario.get("account"):
        return False
    if "--all" in scenario.get("args", []):
        return True
    chosen = [answer["type"].strip() for answer in scenario.get("answers", [])
              if answer.get("when") == CHOOSE_READOUT and "type" in answer]
    return bool(chosen) and chosen[-1] == "1"


def readout_verdicts(scenario: dict[str, Any], family: str, state: Path, since: float, code: int,
                     folder: Path, product: str, prefix: str = "", asked: int = 0) -> list[str]:
    """A readout's other side, after its run (begun at `since`): what the API
    beside the emulator was asked and kept, judged as the fault suites judge
    it (fault_verdicts.readout_api_problems) and kept in `folder` as
    `prefix`api/, as the next run starts it afresh; or for an `api_down`
    scenario's, the readout kept for later, whole; for an `api_links` one,
    the offer the run sent before it too (own_offer_problems). [] for nothing
    wrong, and for a run that is not a readout."""
    if scenario.get("command") != "readout":
        return []
    # A readout's runs alone: not a conversion `prep` made first.
    first = f"--- Stage 1 - {readout.STAGES[0][1]}"
    logs = sorted((log.stat().st_mtime, log) for log in (state / "work/captures/devices").glob("*/runs/*/run.log")
                  if log.stat().st_mtime >= since and first in log.read_text(encoding="utf-8", errors="replace"))
    if not logs:
        return ["FAIL-NOT-ASKED: the readout left no run log"]
    # Each readout of the sitting ("What next": read out another) in a run folder of its own.
    run = "\n".join(fault_verdicts.last_run(log.read_text(encoding="utf-8", errors="replace")) for _, log in logs)
    kept = sorted(path for _, log in logs for path in log.parent.glob("readout*.tar"))
    if scenario.get("api_down"):
        if not kept:
            return ["FAIL-READOUT: not sent, and not kept"]
        missing = fault_verdicts.readout_problems(kept[-1].read_bytes(), family)
        return [f"FAIL-READOUT: the readout kept: {'; '.join(missing)}"] if missing else []
    api = folder / f"{prefix}api"
    shutil.rmtree(api, ignore_errors=True)
    if (state / "api").is_dir():
        shutil.copytree(state / "api", api)
    else:
        api.mkdir()
    if (state / "api-server.out").exists():   # what the API said: an error that stopped it shows there
        shutil.copyfile(state / "api-server.out", api / "api-server.out")
    # This run's requests alone: an S61's API goes on between a scenario's runs
    # (`asked` its log's length before), an S60's starts afresh for each.
    requests = api / "requests.log"
    if requests.exists() and requests.stat().st_size >= asked:
        requests.write_bytes(requests.read_bytes()[asked:])
    # A readout of every device: the account's, and the device; else each of
    # the sitting's readouts of the device alone.
    readouts = len(accounts.ACCOUNTS[scenario["account"]]) + 1 if reads_every_device(scenario) else len(logs)
    runs = firmware_at_start(scenario)
    problems, _ = fault_verdicts.readout_api_problems(run, code == 0, api, readouts, product, family,
                                                      [path.name for path in kept], runs)
    if scenario.get("api_links"):
        problems += fault_verdicts.own_offer_problems(api, product)
    return [f"{verdict}: {why}" for verdict, why in problems]


def judged(scenario: dict[str, Any], folder: Path, code: int, recording: Path | None,
           prefix: str = "") -> list[str]:
    """A run's verdicts, its recording kept in `folder` (names `prefix`ed)."""
    if recording is None or not (recording / "screen.raw").exists():
        return [f"HARNESS: no run to judge (exit {code}); see harness.log"]
    for item in ("screen.raw", "screen.txt", "answers.json", "result.txt", "clipboard.txt", "stopped-at.txt"):
        (folder / f"{prefix}{item}").unlink(missing_ok=True)
        if (recording / item).exists():
            shutil.copyfile(recording / item, folder / f"{prefix}{item}")
    size = tuple(int(part) for part in scenario.get("size", "100x40").split("x"))
    result, copied = folder / f"{prefix}result.txt", folder / f"{prefix}clipboard.txt"
    # The recorder's stand-in keeps what was copied: no file, nothing was.
    stopped = folder / f"{prefix}stopped-at.txt"
    return judge(scenario, (folder / f"{prefix}screen.raw").read_bytes(),
                 json.loads((folder / f"{prefix}answers.json").read_text(encoding="utf-8")), code, size,
                 result.read_text(encoding="utf-8").strip() if result.exists() else None,
                 copied.read_text(encoding="utf-8") if copied.exists() else "",
                 int(stopped.read_text(encoding="utf-8")) if stopped.exists() else None)


def device_of(family: str, mac: str | None) -> kinds.Kind:
    """The kind of device a family's scenarios run on: the S60 copy's, by its
    record's product model, or the S61 the Beken emulator runs."""
    if family == "beken":
        return kinds.SONOFF_S61
    return kinds.for_account((records.read_device(mac) if mac else {}).get("product_model"), None) or kinds.SONOFF_S60


def overall(verdicts: list[str]) -> str:
    """A scenario's one verdict, from all its runs' (judged, readout_verdicts):
    PASS for none; NOT-FIRED where the case was not tried, whatever else was
    found, as in the fault suites; else the first."""
    if not verdicts:
        return "PASS"
    return "NOT-FIRED" if any(verdict.startswith("NOT-FIRED:") for verdict in verdicts) else verdicts[0].split(":")[0]


def run_one(n: int, name: str, family: str, scenario: dict[str, Any], results: Path, mac: str,
            workers: int = 1) -> str:
    folder = results / name
    folder.mkdir(parents=True, exist_ok=True)
    run = run_beken if family == "beken" else run_espressif
    started, since = time.monotonic(), time.time()
    state, product = state_of(family, n), product_of(family, mac)
    # A scenario's first run starts its API afresh (the device's state made anew,
    # or copied from a saved one); its second shares the S61's with the first.
    asked = 0
    try:
        code, recording = run(n, name, scenario, folder, mac)
    except Exception as exc:  # noqa: BLE001 - one scenario's trouble is its own; the rest go on
        code, recording = 125, None
        (folder / "harness.log").open("a", encoding="utf-8").write(f"HARNESS: {type(exc).__name__}: {exc}\n")
        docker("stop", f"autoflash-emu-{n}")
    verdicts = judged(scenario, folder, code, recording)
    if recording is not None:
        verdicts += readout_verdicts(scenario, family, state, since, code, folder, product, asked=asked)
    if not verdicts and scenario.get("again"):
        since = time.time()
        asked = requests_made(state) if family == "beken" else 0
        try:
            code, recording = (run_again_beken if family == "beken" else run_again)(n, scenario, folder, mac)
        except Exception as exc:  # noqa: BLE001 - as above
            code, recording = 125, None
            (folder / "harness.log").open("a", encoding="utf-8").write(f"HARNESS: {type(exc).__name__}: {exc}\n")
        again = {"command": scenario.get("command")} | scenario["again"]
        verdicts = judged(again | {"size": scenario.get("size", "100x40")}, folder, code, recording, "again-")
        if recording is not None:
            verdicts += readout_verdicts(again, family, state, since, code, folder, product, "again-", asked)
        verdicts = [f"{verdict.split(':')[0]}: again: {verdict.split(': ', 1)[1]}" for verdict in verdicts]
    if family == "beken":
        stop_beken(n)   # left running for an `again` not run: the first run did not pass
    verdict = overall(verdicts)
    # Kept beside every batch's folder, so one not sent goes with the next.
    emulator_stats.attended_run(RESULTS, family=family, device=device_of(family, mac).key, name=name,
                                verdict=verdict, seconds=round(time.monotonic() - started), workers=workers)
    line = f"{verdict:<16} {name:<28} {time.monotonic() - started:5.0f}s"
    (folder / "verdict.txt").write_text("\n".join(verdicts or ["PASS"]) + "\n", encoding="utf-8")
    return line + "".join(f"\n    {verdict}" for verdict in verdicts)


def new_results(now: datetime.datetime) -> Path:
    """A batch's own results folder, named for when it began: another batch
    begun the same second (the S61's beside the S60's, say) has that name,
    so this one takes the next free -N after it."""
    name = now.strftime("%Y%m%d-%H%M%S")
    RESULTS.mkdir(parents=True, exist_ok=True)
    for number in range(1, 100):
        folder = RESULTS / (name if number == 1 else f"{name}-{number}")
        try:
            folder.mkdir()
            return folder
        except FileExistsError:
            continue
    raise RuntimeError(f"no results folder free for {name} in {RESULTS}")


def run_all(chosen: list[str], found: dict[str, tuple[str, dict[str, Any]]], results: Path, mac: str | None,
            workers: int, first: int) -> list[str]:
    """Each chosen scenario, `workers` at once (the S61's one at a time,
    after), each on an emulator numbered from `first` that no scenario under
    way is using: a result line for each, printed as it comes."""
    def one(name: str, free: queue.SimpleQueue[int], at_once: int) -> str:
        n = free.get()
        try:
            return run_one(n, name, *found[name], results, mac, at_once)
        finally:
            free.put(n)

    lines = []
    # The S61's on their own, after the S60's: its emulator is slow, and run
    # beside three S60s a readout's device was not found in time.
    ways = (("espressif", max(1, workers)), ("beken", 1))
    for family, at_once in ways:
        if any(found[name][0] == family for name in chosen):
            emulator_instances.refuse_taken(family, range(first, first + at_once))
    for family, at_once in ways:
        planned = [name for name in chosen if found[name][0] == family]
        if planned:
            emulator_stats.began(RESULTS, family=family, suite="attended", device=device_of(family, mac).key,
                                 total=sum(1 for each, _ in found.values() if each == family), planned=len(planned),
                                 cloud="stub", workers=at_once)
        free: queue.SimpleQueue[int] = queue.SimpleQueue()
        for n in range(first, first + at_once):
            free.put(n)
        with concurrent.futures.ThreadPoolExecutor(at_once) as pool:
            jobs = [pool.submit(one, name, free, at_once) for name in chosen if found[name][0] == family]
            for job in concurrent.futures.as_completed(jobs):
                lines.append(job.result())
                print(lines[-1], flush=True)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["list", "run"])
    parser.add_argument("names", nargs="*", help="the scenarios to run (default: all)")
    parser.add_argument("--workers", type=int, default=1, help="scenarios at once, each on an emulator of its own")
    parser.add_argument("--first-instance", type=int, default=FIRST_INSTANCE,
                        help="the first emulator instance it uses, one more for each worker")
    parser.add_argument("--device", help="the backed-up S60 the Espressif scenarios start from, its MAC "
                        "as emu.sh --device takes it")
    args = parser.parse_args()
    found = scenarios()
    if args.command == "list":
        for name, (family, scenario) in found.items():
            print(f"{name:<28} {family:<10} {scenario.get('about', '')}")
        return 0
    chosen = args.names or list(found)
    unknown = [name for name in chosen if name not in found]
    if unknown:
        parser.error(f"no such scenario: {', '.join(unknown)}")
    if not args.device and any(found[name][0] == "espressif" for name in chosen):
        parser.error("an S60's scenarios need --device: the backed-up device the emulator runs a copy of")
    results = new_results(datetime.datetime.now())
    lines = run_all(chosen, found, results, args.device, args.workers, args.first_instance)
    (results / "results.txt").write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    print(emulator_stats.send(RESULTS), flush=True)
    print(f"Results: {results.relative_to(ROOT)}/results.txt")
    return 0 if all(line.startswith("PASS") for line in lines) else 1


if __name__ == "__main__":
    raise SystemExit(main())
