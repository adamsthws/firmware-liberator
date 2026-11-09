#!/usr/bin/env python3
"""Attended runs in the emulators: the tool run as a person sits through it,
every question asked and answered as a scenario's answer set says
(tools/repo/unattended_run.py --attended), and the screen judged at every
answer and at the end - what the fault suites cannot see, as they run the
tool unattended.

A scenario is a JSON file in tools/espressif/emulator/attended/ (an S60's,
in the Espressif emulator) or tools/beken/emulator/attended/ (an S61's):

    {"about": "what it shows",
     "start": "fresh" | a saved plug (unattended-test.sh --from),
     "prep": [...], "args": [...], "size": "100x40", "minutes": 30,
     "answers": [{"when": regex, "type": keys, "times": n, "optional": bool}, ...],
     "expect": {"exit": 0, "result": "Converted", "screen": true},
     "again": {"answers": [...], "args": [...], "expect": {...}}}

`again`: the tool run once more on the plug as the first run left it, with
no --new - a run stopped part way, offered back ("did not finish") - judged
as the first is.

`prep`, before the run, on the plug (Espressif only): ["convert", FIRMWARE]
first, if at all (the plug converted to it, unattended); "sudo forgets" (sudo
remembers no password in the emulator, so it asks again at every use - the
end of the run too); ["tasmota password", PASSWORD] (Tasmota's web password
set); ["locked esphome", "login" | "login and updates"] (this project's
ESPHome with a web login, admin and "test-login-1", and the made-up API
encryption key LOCKED_KEY; and with "updates", taking only encrypted
updates). An answer is typed at its question only (unattended_run.Answer);
one `optional` need not be asked.

The verdict, from the recording alone (judge):
    PASS              ended as expected, every answer typed, nothing wrong
    FAIL-RESULT       ended otherwise: its exit code, or its report's Result
    FAIL-UNANSWERED   it ended at a question no answer covers: new, or reworded
    FAIL-UNUSED       an answer never typed: its question no longer asked
    FAIL-QUESTION     as a question was answered: no "(q to quit)" on it, a
                      row the terminal wrapped itself, or a question shown
                      twice - an earlier try left on the screen
    FAIL-SCREEN       the screen it ended on (unattended_run.stage_problems)

    tools/espressif/emulator/attended_suite.py list
    tools/espressif/emulator/attended_suite.py run [NAME ...] [--workers N] --plug MAC

Its results, and each run's recording, go to captures/emulation/attended/<time>/.
Run on a computer with the emulators (docs/emulator.md); each scenario runs
on an emulator of its own (instances 61 and on), --workers at once - the
S61's after, one at a time.
"""

from __future__ import annotations

import argparse
import base64
import codecs
import concurrent.futures
import datetime
import json
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
from tools.repo import unattended_run  # noqa: E402

SCENARIOS = {"espressif": ROOT / "tools/espressif/emulator/attended",
             "beken": ROOT / "tools/beken/emulator/attended"}
RESULTS = ROOT / "captures/emulation/attended"
FIRST_INSTANCE = 61
SIZE = (100, 40)
# The made-up key of the locked ESPHome builds (build_locked).
LOCKED_KEY = base64.b64encode(bytes(range(32))).decode()
LOCKED_LOGIN = ("admin", "test-login-1")
RESULT = re.compile(r"Result:\s+(\S.*?)(?:\s{2,}|$)", re.M)
QUIT_FIRST = [{"when": "Which device is this run for|Press any key", "type": "q\r"}]
PLUG = "192.168.60.50"


def scenarios() -> dict[str, tuple[str, dict[str, Any]]]:
    """Every scenario, by name: its family's, and what its file says."""
    found = {}
    for family, folder in SCENARIOS.items():
        for path in sorted(folder.glob("*.json")):
            found[path.stem] = (family, json.loads(path.read_text(encoding="utf-8")))
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
          size: tuple[int, int] = SIZE) -> list[str]:
    """The verdicts of a run, from its recording (screen.raw), the answers as
    the recorder kept them (answers.json, with typed_at) and its exit code:
    empty for a PASS."""
    expect = scenario.get("expect", {})
    verdicts = []
    final = screen_at(raw, len(raw), size)
    text = final.render(colour=False)
    asked = unattended_run.question(final)
    if unattended_run.waiting(asked) and "(q to quit)" in asked:
        waiting = next(row for row in reversed(asked.splitlines()) if "(q to quit)" in row)
        verdicts.append(f"FAIL-UNANSWERED: {waiting.strip()[:70]!r}")
    if code != expect.get("exit", 0):
        verdicts.append(f"FAIL-RESULT: exit {code}, not {expect.get('exit', 0)}")
    if expect.get("result"):
        found = RESULT.findall(text)
        if not found or not found[-1].strip().startswith(expect["result"]):
            verdicts.append(f"FAIL-RESULT: Result {found[-1].strip() if found else 'none'!r}, "
                            f"not {expect['result']!r}")
    given = {(item["when"], item["type"]): item for item in scenario["answers"]}
    for answer in answers:
        if not answer["typed_at"] and not given.get((answer["when"], answer["type"]), {}).get("optional"):
            verdicts.append(f"FAIL-UNUSED: {answer['when']!r}")
    wrapped = 0
    # A key typed anywhere (a Ctrl-C as a stage shows) answers no question.
    for at in sorted(at for answer in answers if not answer.get("anywhere") for at in answer["typed_at"]):
        screen = screen_at(raw, at, size)
        for problem in question_problems(screen, wrapped):
            verdicts.append(f"FAIL-QUESTION: {problem}")
        wrapped = screen.wrapped
    if expect.get("screen", True):
        # The window as it was left, not its scrollback: a run gone on to
        # another device scrolls the last one up into it, with blank rows.
        window = "\n".join(text.rstrip("\n").split("\n")[-size[1]:])
        verdicts += [f"FAIL-SCREEN: {problem}" for problem in unattended_run.stage_problems(window)]
    return verdicts


# ---------------------------------------------------------------- running


def run_answers(scenario: dict[str, Any], folder: Path, name: str = "answers-given.json") -> Path:
    """The scenario's answers as the recorder takes them (no `optional`)."""
    path = folder / name
    path.write_text(json.dumps([{key: value for key, value in item.items() if key != "optional"}
                                for item in scenario["answers"]], indent=1), encoding="utf-8")
    return path


def docker(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=check)


def in_container(n: int, code: str) -> str:
    """Python in emulator `n`'s container, as the tool runs there."""
    import os
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


def wrapper(n: int, scenario: dict[str, Any], *more: str, mac: str) -> list[str]:
    return [str(ROOT / "tools/espressif/emulator/unattended-test.sh"), f"--instance={n}", f"--plug={mac}",
            "--stub-cloud", *more]


def prepare(n: int, scenario: dict[str, Any], folder: Path, log, mac: str) -> bool:
    """The plug as the scenario starts it, before its run: from where it says,
    then the tool quit at its first question with the emulator kept, for
    what `prep` does to the plug. False when that could not be done."""
    start = scenario.get("start", "fresh")
    actions = [action for action in scenario.get("prep", []) if action != "sudo forgets"]
    first = ["--fresh"] if start == "fresh" else [f"--from={start}"]
    if not actions:
        return True   # its run starts it, from where it says
    if actions[0][0] == "convert":
        # Converted first, unattended: a plug saved on one firmware, wanted on another.
        code = subprocess.call(wrapper(n, scenario, *first, "--timeout=40", "--", "--new", "--firmware",
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
    subprocess.call(wrapper(n, scenario, *first, "--keep", "--timeout=8", f"--answers={quit_first}",
                            "--", "--new", mac=mac), stdout=log, stderr=subprocess.STDOUT)
    for action in actions:
        kind, value = action
        if kind == "tasmota password":
            for _ in range(12):   # the plug may still be starting
                # As the tool sends Tasmota a command: a plain request was hung up on.
                said = in_container(n, "from tools.autoflash.device import Device; "
                                    f"print(Device('{PLUG}').command('Backlog WebPassword {value}; Restart 1', "
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
            said = in_container(n, f"from tools.autoflash import esphome_ota; esphome_ota.upload('{PLUG}', "
                                "open('locked.ota.bin', 'rb').read()); print('sent')")
            target.unlink(missing_ok=True)
            log.write(f"PREP locked esphome ({value}): {said}\n")
            # A minute on its new release, or a power cut takes the plug back to the one it had.
            time.sleep(90)
            check = in_container(n, "import urllib.request, urllib.error\ntry:\n urllib.request.urlopen("
                                 f"'http://{PLUG}/', timeout=5); print(200)\n"
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
        out = subprocess.run(command, capture_output=True, text=True)
        stop.set()
        log.write(out.stdout + out.stderr)
        docker("stop", f"autoflash-emu-{n}")
    found = re.search(r"Exit code (-?\d+)", out.stdout)
    where = re.search(r"On this computer: (\S+)", out.stdout)
    return (int(found.group(1)) if found else out.returncode), (ROOT / where.group(1) if where else None)


def run_again(n: int, scenario: dict[str, Any], folder: Path, mac: str) -> tuple[int, Path | None]:
    """The tool once more on emulator `n`'s plug as the first run left it:
    no --new, so a run left unfinished is offered back."""
    again = scenario["again"]
    with open(folder / "harness.log", "a", encoding="utf-8") as log:
        command = wrapper(n, scenario, f"--timeout={again.get('minutes', 30)}",
                          f"--size={scenario.get('size', '100x40')}",
                          f"--answers={run_answers(again, folder, 'again-answers-given.json')}", "--",
                          *again.get("args", []), mac=mac)
        log.write("--- " + " ".join(command[1:]) + "\n")
        log.flush()
        out = subprocess.run(command, capture_output=True, text=True)
        log.write(out.stdout + out.stderr)
        docker("stop", f"autoflash-emu-{n}")
    found = re.search(r"Exit code (-?\d+)", out.stdout)
    where = re.search(r"On this computer: (\S+)", out.stdout)
    return (int(found.group(1)) if found else out.returncode), (ROOT / where.group(1) if where else None)


def run_beken(n: int, name: str, scenario: dict[str, Any], folder: Path, mac: str) -> tuple[int, Path | None]:
    """The scenario on Beken emulator `n`: a fresh S61, then the tool on the
    computer on its Wi-Fi (beken-emu.sh computer), its eWeLink sign-in typed."""
    emu = str(ROOT / "tools/beken/emulator/beken-emu.sh")
    state = ROOT / f"captures/emulation/beken/state-{n}"
    with open(folder / "harness.log", "a", encoding="utf-8") as log:
        subprocess.call([emu, "--instance", str(n), "--fresh", "plug"], stdout=log, stderr=subprocess.STDOUT,
                        timeout=1200)
        (state / "work/captures").mkdir(parents=True, exist_ok=True)
        shutil.copyfile(run_answers(scenario, folder), state / "work/captures/answers.json")
        command = [emu, "--instance", str(n), "computer", "tools/repo/unattended_run.py",
                   "--size", scenario.get("size", "100x40"), "--timeout", str(scenario.get("minutes", 60)),
                   "--attended", "--answers", "captures/answers.json", "--save", "captures/unattended/attended",
                   *(["--command", "readout"] if scenario.get("command") == "readout" else []),
                   "--", "--new", *scenario.get("args", [])]
        log.write("--- " + " ".join(command[1:]) + "\n")
        log.flush()
        out = subprocess.run(command, capture_output=True, text=True)
        log.write(out.stdout + out.stderr)
        for container in docker("ps", "--format", "{{.Names}}").stdout.split():
            if container.startswith(f"autoflash-beken-emu-{n}"):
                docker("stop", "-t", "30", container)
    found = re.search(r"Exit code (-?\d+)", out.stdout)
    return (int(found.group(1)) if found else out.returncode), state / "work/captures/unattended/attended"


def judged(scenario: dict[str, Any], folder: Path, code: int, recording: Path | None,
           prefix: str = "") -> list[str]:
    """A run's verdicts, its recording kept in `folder` (names `prefix`ed)."""
    if recording is None or not (recording / "screen.raw").exists():
        return [f"HARNESS: no run to judge (exit {code}); see harness.log"]
    for item in ("screen.raw", "screen.txt", "answers.json"):
        if (recording / item).exists():
            shutil.copyfile(recording / item, folder / f"{prefix}{item}")
    size = tuple(int(part) for part in scenario.get("size", "100x40").split("x"))
    return judge(scenario, (folder / f"{prefix}screen.raw").read_bytes(),
                 json.loads((folder / f"{prefix}answers.json").read_text(encoding="utf-8")), code, size)


def run_one(n: int, name: str, family: str, scenario: dict[str, Any], results: Path, mac: str) -> str:
    folder = results / name
    folder.mkdir(parents=True, exist_ok=True)
    run = run_beken if family == "beken" else run_espressif
    started = time.monotonic()
    try:
        code, recording = run(n, name, scenario, folder, mac)
    except Exception as exc:  # noqa: BLE001 - one scenario's trouble is its own; the rest go on
        code, recording = 125, None
        (folder / "harness.log").open("a", encoding="utf-8").write(f"HARNESS: {type(exc).__name__}: {exc}\n")
        docker("stop", f"autoflash-emu-{n}")
    verdicts = judged(scenario, folder, code, recording)
    if not verdicts and scenario.get("again") and family == "espressif":
        try:
            code, recording = run_again(n, scenario, folder, mac)
        except Exception as exc:  # noqa: BLE001 - as above
            code, recording = 125, None
            (folder / "harness.log").open("a", encoding="utf-8").write(f"HARNESS: {type(exc).__name__}: {exc}\n")
        verdicts = [f"{verdict.split(':')[0]}: again: {verdict.split(': ', 1)[1]}"
                    for verdict in judged(scenario["again"] | {"size": scenario.get("size", "100x40")},
                                          folder, code, recording, "again-")]
    line = f"{'PASS' if not verdicts else verdicts[0].split(':')[0]:<16} {name:<28} {time.monotonic() - started:5.0f}s"
    (folder / "verdict.txt").write_text("\n".join(verdicts or ["PASS"]) + "\n", encoding="utf-8")
    return line + "".join(f"\n    {verdict}" for verdict in verdicts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["list", "run"])
    parser.add_argument("names", nargs="*", help="the scenarios to run (default: all)")
    parser.add_argument("--workers", type=int, default=1, help="scenarios at once, each on an emulator of its own")
    parser.add_argument("--plug", help="the backed-up S60 the Espressif scenarios start from, its MAC "
                        "as emu.sh --plug takes it")
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
    if not args.plug and any(found[name][0] == "espressif" for name in chosen):
        parser.error("an S60's scenarios need --plug: the backed-up plug the emulator runs a copy of")
    results = RESULTS / datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    results.mkdir(parents=True)
    lines = []
    # The S61's on their own, after the S60's: its emulator is slow, and run
    # beside three S60s a readout's plug was not found in time.
    for family, workers in (("espressif", max(1, args.workers)), ("beken", 1)):
        with concurrent.futures.ThreadPoolExecutor(workers) as pool:
            jobs = [pool.submit(run_one, FIRST_INSTANCE + index, name, *found[name], results, args.plug)
                    for index, name in enumerate(chosen) if found[name][0] == family]
            for job in concurrent.futures.as_completed(jobs):
                lines.append(job.result())
                print(lines[-1], flush=True)
    (results / "results.txt").write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    print(f"Results: {results.relative_to(ROOT)}/results.txt")
    return 0 if all(line.startswith("PASS") for line in lines) else 1


if __name__ == "__main__":
    raise SystemExit(main())
