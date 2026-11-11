#!/usr/bin/env python3
"""Run a whole conversion unattended, and keep the screen it drew for review.

    python3 tools/repo/unattended_run.py [--size 100x40] [--save DIR] [--command readout] [-- run options...]

The conversion (autoflash/cli.py run --unattended) runs on a terminal of its
own (a pseudo-terminal), so it draws exactly what a person would see - the
colours, the stage lines redrawn in place, the notes taken away once read.
Everything it writes is recorded, and afterwards the screen as it was left
is rebuilt from the recording and printed, colours and all.

The eWeLink account comes from .env (see .env.example), as does this
computer's sudo password where sudo would ask for one.  Anything after `--`
goes to the run itself: --device-ip, --new, --verbose and so on. --command
readout makes a readout instead (cli.py readout --unattended), as the fault
suites' readout scenarios do.

Saved, under captures/unattended/<time>/ unless --save says otherwise:
    screen.ansi   the final screen, with its colours: `cat` or `less -R` it
    screen.txt    the same without colours, to read or compare anywhere
    screen.raw    every byte written, as written: `cat` replays it in a terminal

Linux and macOS only (Windows has no pseudo-terminal of this kind); there,
start.cmd run --unattended runs a conversion just as unattended, unrecorded.
"""

from __future__ import annotations

import argparse
import codecs
import contextlib
import json
import os
import re
import select
import shutil
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / "tools" / "autoflash" / "cli.py"

# A control sequence (CSI), an operating-system command (OSC: links, titles),
# any other escape, a control character, or a run of ordinary text.
TOKEN = re.compile(
    r"\x1b\[(?P<params>[0-9;?]*)(?P<final>[@-~])"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[@-_]"
    r"|(?P<control>[\x00-\x1f\x7f])"
    r"|(?P<text>[^\x00-\x1f\x7f\x1b]+)"
)


class Screen:
    """Just enough of a terminal to rebuild what one shows from what was
    written to it: the sequences the conversion's output uses - colours,
    carriage returns, erasing a line, moving up a row - with lines wrapping
    at the width and the scrollback kept.  As on a real terminal, the cursor
    cannot go up past the top of the window into the scrollback."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.rows: list[list[tuple[str, str]]] = [[]]
        self.row = self.col = 0
        self.style = ""        # the colour sequences in force, as written
        self.wrap_next = False  # at the last column: the next character wraps
        # Rows the terminal wrapped itself: written past its edge, as output.py
        # never should (it wraps each row first).
        self.wrapped = 0
        self.continued: set[int] = set()   # rows carrying on a line the terminal wrapped
        # Rows wiped whole (erase line 2, erase below): a question asked again
        # where one was answered is drawn on rows wiped first.
        self.wiped = 0

    def feed(self, text: str) -> None:
        for match in TOKEN.finditer(text):
            if match.group("text") is not None:
                for char in match.group("text"):
                    self._put(char)
            elif match.group("control") is not None:
                self._control(match.group("control"))
            elif match.group("final") is not None:
                self._csi(match.group("params"), match.group("final"))
            # Anything else - links, titles - leaves the text unchanged.

    def _top(self) -> int:
        return max(0, len(self.rows) - self.height)

    def _line(self) -> list[tuple[str, str]]:
        while len(self.rows) <= self.row:
            self.rows.append([])
        return self.rows[self.row]

    def _put(self, char: str) -> None:
        if self.wrap_next:
            self.row, self.col, self.wrap_next = self.row + 1, 0, False
            self.wrapped += 1
            self.continued.add(self.row)
        line = self._line()
        while len(line) < self.col:
            line.append((" ", ""))
        cell = (char, self.style)
        if self.col < len(line):
            line[self.col] = cell
        else:
            line.append(cell)
        if self.col == self.width - 1:
            self.wrap_next = True
        else:
            self.col += 1

    def _control(self, char: str) -> None:
        if char == "\n":
            self.row += 1
            self._line()
            self.continued.discard(self.row)   # a line of its own from here
        elif char == "\r":
            self.col = 0
        elif char == "\b":
            self.col = max(0, self.col - 1)
        elif char == "\t":
            self.col = min(self.width - 1, (self.col // 8 + 1) * 8)
        else:
            return  # a bell, say: nothing to see
        self.wrap_next = False

    def _csi(self, params: str, final: str) -> None:
        numbers = [int(part) if part.isdigit() else 0 for part in params.lstrip("?").split(";")]
        count = max(1, numbers[0])
        self.wrap_next = False
        if final == "m":
            # Kept as written; a reset (or nothing) clears what came before.
            self.style = "" if params in ("", "0") else self.style + f"\x1b[{params}m"
        elif final in "AF":  # up; F also to the start of the row
            self.row = max(self._top(), self.row - count)
            if final == "F":
                self.col = 0
        elif final in "BE":  # down; E also to the start of the row
            self.row += count
            self._line()
            if final == "E":
                self.col = 0
        elif final == "C":
            self.col = min(self.width - 1, self.col + count)
        elif final == "D":
            self.col = max(0, self.col - count)
        elif final == "G":
            self.col = min(self.width - 1, count - 1)
        elif final == "K":
            line = self._line()
            if numbers[0] == 0:
                del line[self.col:]
            elif numbers[0] == 1:
                line[:self.col + 1] = [(" ", "")] * min(len(line), self.col + 1)
            else:
                line.clear()
                self.continued.discard(self.row)   # cleared, to be written afresh
                self.wiped += 1
        elif final == "J":
            self.wiped += 1
            if numbers[0] == 0:
                del self._line()[self.col:]
                del self.rows[self.row + 1:]
            elif numbers[0] in (2, 3):
                for line in self.rows[self._top():]:
                    line.clear()
        # Anything else - hiding the cursor, say - changes no text.

    def last_row(self) -> int:
        """Where the last row that holds anything is, counted from the top."""
        rows = self.render(colour=False).split("\n")
        return max((index for index, row in enumerate(rows) if row.strip()), default=0)

    def lines(self) -> list[str]:
        """The lines written, plain, each whole however many rows the
        terminal wrapped it over, the blank ones left out."""
        found: list[str] = []
        for index, row in enumerate(self.render(colour=False).split("\n")):
            if index in self.continued and found:
                found[-1] += row
            elif row.strip():
                found.append(row)
        return found

    def render(self, colour: bool = True) -> str:
        """Every row, scrollback and all, without the blank rows at the end."""
        rows = []
        for line in self.rows:
            while line and line[-1] == (" ", ""):
                line = line[:-1]
            text, style = "", ""
            for char, cell_style in line:
                if colour and cell_style != style:
                    text += "\x1b[0m" + cell_style
                    style = cell_style
                text += char
            if colour and style:
                text += "\x1b[0m"
            rows.append(text.rstrip() if not colour else text)
        while rows and not rows[-1].strip():
            rows.pop()
        return "\n".join(rows) + "\n"


# A stage's line as it is left on the screen: "Stage 13 - Staging startup
# firmware   - Stopped", its number padded and its name to the longest's.
STAGE_ROW = re.compile(r"^ *Stage +(?P<number>\d+) - (?P<name>.+?) +- (?P<outcome>[A-Z][a-z]+(?: [a-z]+)*)", re.M)


# "What next", answered: a run that finished asks it (cli.what_next), and the
# next run in the sitting starts at the top of the window, the last one's
# rows scrolled up above it with a window of blank rows (output.start_at_top).
NEXT_RUN = re.compile(r"(?m)^ *What next\b.*$")


# What a stage's line says while it runs: "Retrying" for a step done again.
UNDER_WAY = ("Underway", "Retrying")
# What a stage that passed ends with: all it showed is taken off the screen.
PASSED = ("Complete", "Already done", "Skipped")


def stage_problems(screen: str) -> list[str]:
    """What a run left on `screen` (Screen.render) that it should have taken
    away as it moved on: [] for nothing.  A stage that passed leaves one
    line, its outcome, with what it kept under it; one that stopped leaves
    what it showed above its line, as that is why.  So it is a problem when

    - a stage's outcome is shown twice: a try that failed left on the
      screen when the stage was tried again, or a stage done again after a
      later one found it had not taken (GoBack) drawn under its first go;
    - an "Underway" (or "Retrying") line has another stage's after it, not its own outcome:
      a stage that never ended;
    - a stage that passed still has its "Underway" line above its outcome:
      what it showed under that line - notes, instructions, a part that went
      again - was left on the screen;
    - two blank rows follow one another.

    Each run of a sitting is looked at on its own (NEXT_RUN): the next one's
    stages are its own, not the last one's done again."""
    problems: list[str] = []
    for run in NEXT_RUN.split(screen):
        problems += [problem for problem in _run_problems(run) if problem not in problems]
    return problems


def _run_problems(screen: str) -> list[str]:
    """stage_problems() of one run's rows."""
    problems: list[str] = []
    underway: str | None = None
    ended: set[str] = set()
    shown_under: set[str] = set()   # stages with an "Underway" line on the screen
    for row in STAGE_ROW.finditer(screen):
        stage, outcome = f"Stage {row['number']} - {row['name']}", row["outcome"]
        if underway not in (None, stage):
            problems.append(f"{underway}: left underway when the next stage began")
        if outcome in UNDER_WAY:
            underway = stage
            shown_under.add(stage)
            continue
        underway = None
        if stage in ended:
            problems.append(f"{stage}: shown {outcome.lower()}, after an earlier outcome - "
                            "its first go was left on the screen")
        ended.add(stage)
        if outcome in PASSED and stage in shown_under:
            problems.append(f"{stage}: passed, with what it showed while under way left above it")
        shown_under.discard(stage)
    if "\n\n\n" in "\n".join(line.rstrip() for line in screen.splitlines()).strip("\n"):
        problems.append("two blank rows one after another")
    return problems


def parse_size(text: str) -> tuple[int, int]:
    match = re.fullmatch(r"(\d+)x(\d+)", text)
    if not match or int(match.group(1)) < 40 or int(match.group(2)) < 20:
        raise argparse.ArgumentTypeError("COLUMNSxROWS, at least 40x20 - 100x40, say")
    return int(match.group(1)), int(match.group(2))


class Answer:
    """A person at the keyboard, as far as one question goes: when the
    question the run shows, since the last answer, matches `when` (a regular
    expression, on the text without its colours), `type` is typed - "\r" is
    Enter - up to `times` times.  The question is its prompt's row (question):
    not the notes above it, which can say a question's words - seen, "your
    eWeLink account" in the firmware question's taken for the sign-in.
    `anywhere`: typed when `when` shows anywhere on the screen, a question
    or not - a Ctrl-C ("\x03") as a stage shows, say."""

    def __init__(self, when: str, type: str, times: int = 1, anywhere: bool = False) -> None:
        self.when, self.type, self.left, self.anywhere = re.compile(when), type, times, anywhere
        self.typed_at: list[int] = []   # how many bytes the run had written each time it was typed

    @classmethod
    def load(cls, path: Path) -> list["Answer"]:
        """A list of {"when": ..., "type": ..., "times": ...}, as JSON."""
        return [cls(**item) for item in json.loads(path.read_text(encoding="utf-8"))]


def question(screen: "Screen") -> str:
    """The question waiting: the last line on the screen, its prompt - with,
    for a password, the field under it (prompts.FIELD).  Read off the screen, not the text written: a wrong
    try is taken off by moving the cursor up, which text does not show."""
    lines = screen.lines()
    # A password's field is a row of its own, under its prompt; any other
    # question is its prompt's line alone, however many rows it wraps over -
    # the line above can be the question before, answered (seen: "What now: 1"
    # taken for "What now" asked again).
    # The same for a prompt too long to share its row with "(q to quit)",
    # which then goes on a row of its own under it (output.question).
    field = lines and lines[-1].strip().startswith((">", "(q to quit)"))
    return "\n".join(lines[-2:] if field else lines[-1:])


def waiting(asked: str) -> bool:
    """Whether a question is waiting for its answer: its prompt ends the
    last row - "...: ", a password's "> ", "Press any key (q to quit)" -
    not an answer typed after it (seen, a password's field filling in was
    taken for its question asked again)."""
    return asked.rstrip().endswith((":", ">", ")"))


# What a terminal shows but the text does not say: colours, cursor moves.
CONTROL = re.compile(r"\x1b\[[0-9;?]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-_]")


def record(command: list[str], size: tuple[int, int], live: bool,
           timeout: float | None, answers: list[Answer] = ()) -> tuple[int, bytes]:
    """Run `command` on a pseudo-terminal of `size`; its exit code and every
    byte it wrote.  `answers`: typed as their questions show (Answer)."""
    import fcntl
    import pty
    import struct
    import termios

    environment = {**os.environ, "COLUMNS": str(size[0]), "LINES": str(size[1])}
    # The maintainer's own testing: its report, if one is sent (none is in the
    # emulator), is marked so and left out of the public figures.
    environment.setdefault("AUTOFLASH_REPORT_TAG", "dev")
    environment.pop("NO_COLOR", None)
    if environment.get("TERM", "dumb") in ("", "dumb"):
        environment["TERM"] = "xterm-256color"
    pid, fd = pty.fork()
    if pid == 0:  # the child: the conversion, on the new terminal, sized before it looks
        try:
            fcntl.ioctl(0, termios.TIOCSWINSZ, struct.pack("HHHH", size[1], size[0], 0, 0))
            os.execve(command[0], command, environment)
        finally:
            os._exit(127)
    started, written, stopping = time.monotonic(), bytearray(), False
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    screen = Screen(*size)
    answered = None   # the question last answered, as it was on the screen
    while True:
        try:
            if timeout and not stopping and time.monotonic() - started > timeout:
                stopping = True
                os.kill(pid, signal.SIGTERM)  # the tool tidies up on SIGTERM too
            if not select.select([fd], [], [], 0.5)[0]:
                continue
            try:
                chunk = os.read(fd, 65536)
            except OSError:  # EIO: the conversion has exited and its terminal closed
                break
            if not chunk:
                break
            written += chunk
            if live:
                os.write(sys.stdout.fileno(), chunk)
            if answers:
                text = decoder.decode(chunk)
                screen.feed(text)
                asked = question(screen)
                # A question waits with the cursor on its row: not once an
                # Enter has moved it on, nor as its row is being redrawn.
                at_prompt = screen.row == screen.last_row()
                # Where the question's prompt is: the row above for a password's
                # field or a hint under it - drawn after the prompt, perhaps once
                # it was answered (seen: answered on "Password:" alone, then
                # again as its field appeared under it).
                prompt_row = screen.row - ("\n" in asked)
                # Only a question waiting is weighed: as the answered one's row
                # is redrawn, none is (its field erased for a moment, say).
                if answered is not None and at_prompt and waiting(asked) \
                        and (prompt_row != answered[0] or screen.wiped > answered[1]):
                    # Another question: on another row, or on rows wiped since
                    # - one asked again just as it was, a password not taken,
                    # is answered again. Not the one answered, redrawn as the
                    # tool takes the answer (a password's field filling in):
                    # seen looking as it did, and answered twice.
                    answered = None
                for answer in answers:
                    if answer.anywhere:
                        if answer.left and answer.when.search(screen.render(colour=False)):
                            time.sleep(0.5)
                            os.write(fd, answer.type.encode("utf-8"))
                            answer.typed_at.append(len(written))
                            answer.left -= 1
                            break
                        continue
                    if at_prompt and waiting(asked) and answered is None and answer.left \
                            and answer.when.search(asked):
                        time.sleep(0.5)   # as a person reads it
                        os.write(fd, answer.type.encode("utf-8"))
                        answer.typed_at.append(len(written))
                        answer.left -= 1
                        answered = (prompt_row, screen.wiped)
                        break
        except KeyboardInterrupt:
            os.kill(pid, signal.SIGINT)  # passed on: the tool stops cleanly, as at a keyboard
    os.close(fd)
    _, status = os.waitpid(pid, 0)
    return os.waitstatus_to_exitcode(status), bytes(written)


# What the tool can be run to do with nobody there (cli.py's commands).
COMMANDS = ("run", "readout")


def tool_command(command: str, extra: list[str], attended: bool = False) -> list[str]:
    """The tool's command line: `command` (COMMANDS), unattended unless
    `attended` (its questions then answered by --answers), with `extra`."""
    return [sys.executable, str(TOOL), command, *([] if attended else ["--unattended"]), *extra]


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--size", type=parse_size,
                        help="the terminal the run gets, COLUMNSxROWS (default: this one's, "
                        "or 100x40 when there is none)")
    parser.add_argument("--save", type=Path, help="where to keep the recording "
                        "(default: captures/unattended/<time>/)")
    parser.add_argument("--timeout", type=float, metavar="MINUTES",
                        help="stop the run (as Ctrl-C would) if it is still going after this long")
    parser.add_argument("--command", choices=COMMANDS, default="run",
                        help="what the tool does unattended: a conversion (run, the default) or a readout")
    parser.add_argument("--attended", action="store_true",
                        help="run the tool as a person would sit through it, not --unattended: "
                        "its questions are asked, and answered by --answers")
    parser.add_argument("--answers", type=Path, metavar="FILE",
                        help="what to type at each question, for --attended: a JSON list of "
                        '{"when": regex, "type": text, "times": n}, "\\r" for Enter')
    parser.add_argument("run_options", nargs=argparse.REMAINDER,
                        help="after --: options for the run itself, e.g. -- --device-ip 192.168.99.50")
    args = parser.parse_args()
    if not hasattr(os, "fork"):
        print("This needs Linux or macOS. On Windows, start.cmd run --unattended "
              "runs a conversion unattended, without the recording.")
        return 2

    extra = args.run_options[1:] if args.run_options[:1] == ["--"] else args.run_options
    live = sys.stdout.isatty()
    # Python 3.10 gives 0 for a side a terminal does not report; 3.11 the fallback.
    size = args.size or (tuple(n or d for n, d in zip(shutil.get_terminal_size(), (80, 24)))
                         if live else (100, 40))
    save = args.save or ROOT / "captures" / "unattended" / time.strftime("%Y%m%d-%H%M%S")
    command = tool_command(args.command, extra, attended=args.attended)
    answers = Answer.load(args.answers) if args.answers else []

    code, written = record(command, size, live, args.timeout * 60 if args.timeout else None, answers)

    screen = Screen(*size)
    screen.feed(codecs.decode(written, "utf-8", errors="replace"))
    save.mkdir(parents=True, exist_ok=True)
    (save / "screen.raw").write_bytes(written)
    (save / "screen.ansi").write_text(screen.render(), encoding="utf-8")
    (save / "screen.txt").write_text(screen.render(colour=False), encoding="utf-8")
    if answers:
        # Where each answer was typed, in screen.raw's bytes: to see what each
        # question left on the screen once answered.
        (save / "answers.json").write_text(json.dumps(
            [{"when": answer.when.pattern, "type": answer.type, "typed_at": answer.typed_at,
              **({"anywhere": True} if answer.anywhere else {})} for answer in answers],
            indent=1), encoding="utf-8")

    shown = save.resolve()
    with contextlib.suppress(ValueError):
        shown = shown.relative_to(Path.cwd())
    if live:
        # The screen above is the run's own; the rule marks where it ends.
        print("-" * min(size[0], 72))
    else:
        rule = "=" * 72
        print(f"{rule}\nThe screen at the end of the run ({size[0]}x{size[1]}):\n{rule}")
        sys.stdout.write(screen.render(colour=os.environ.get("NO_COLOR") is None))
        print(rule)
    outcome = {0: "finished", 130: "stopped at a question, or by Ctrl-C",
               143: "stopped by --timeout"}.get(code, "failed")
    print(f"Exit code {code} - {outcome}")
    for problem in stage_problems(screen.render(colour=False)):
        print(f"SCREEN: {problem}")
    print(f"Kept in {shown}/: screen.ansi (cat it), screen.txt, screen.raw")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
