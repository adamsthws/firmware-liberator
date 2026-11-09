"""How tall the tool's fixed text is on the screen: each section (a "-- "
heading and its lines, prompts.section) and each warning box, read from the
code and wrapped as output.py wraps it in an 80-column window.  A stage
taller than the window cannot be taken off the screen again once it has
passed (output._erase): the rows scrolled away are out of reach.  Seen in
the fault suite, a stage 48 rows tall left all of itself behind."""

import ast
import unittest
from pathlib import Path

from tools.autoflash import output, prompts

ROOT = Path(__file__).resolve().parents[2]
COLUMNS = 80
# Half the shortest window the tool goes on in without asking for a taller
# one: the rest is for the stage lines above and the question below.
MOST_ROWS = output.MIN_ROWS // 2
# A value only known at run time - a name, an address, an error - as long
# as a typical one.
FILLED = "x" * 12


def text(node: ast.expr) -> str:
    """The longest a line can be, its run-time parts filled in."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else FILLED for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return text(node.left) + text(node.right)
    if isinstance(node, ast.IfExp):
        return max(text(node.body), text(node.orelse), key=len)
    return FILLED


def lines(node: ast.expr, names: dict[str, list[ast.expr]]) -> list[str] | None:
    """The most lines a section's body can hold; None when it is made at run
    time (a function's argument, another function's result).  A part made at
    run time inside a list - a device for each one found, say - counts as one."""
    if isinstance(node, (ast.List, ast.Tuple)):
        found: list[str] = []
        for item in node.elts:
            if isinstance(item, ast.Starred):
                found += lines(item.value, names) or [FILLED]
            else:
                found.append(text(item))
        return found
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = lines(node.left, names), lines(node.right, names)
        return None if left is None or right is None else left + right
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mult):
        return lines(node.left, names)  # [""] * bool(...): at most once
    if isinstance(node, ast.BoolOp):
        options = [lines(value, names) for value in node.values]
        return max((option for option in options if option is not None), key=len, default=None)
    if isinstance(node, ast.IfExp):
        options = [lines(node.body, names), lines(node.orelse, names)]
        return max((option for option in options if option is not None), key=len, default=None)
    if isinstance(node, ast.Name) and node.id in names:
        options = [lines(value, names) for value in names[node.id]]
        return max((option for option in options if option is not None), key=len, default=None)
    return None


def assignments(function: ast.AST) -> dict[str, list[ast.expr]]:
    """What each name in a function is set to: `body += [...]` as the sum."""
    names: dict[str, list[ast.expr]] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    names.setdefault(target.id, []).append(node.value)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            names[node.target.id] = [ast.BinOp(left=value, op=ast.Add(), right=node.value)
                                     for value in names.get(node.target.id, [ast.List(elts=[])])]
    return names


def sections() -> list[tuple[str, str, list[str] | None]]:
    """(where, heading, its lines) for every section and warning box in the tool."""
    found = []
    for path in sorted((ROOT / "tools" / "autoflash").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            names = assignments(function)
            for call in ast.walk(function):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                        and call.func.attr in ("section", "warning_box") and len(call.args) >= 2):
                    where = f"{path.relative_to(ROOT)}:{call.lineno}"
                    found.append((where, text(call.args[0]), lines(call.args[1], names)))
    return sorted(set((where, title, tuple(body) if body is not None else None)
                      for where, title, body in found))


def rows(title: str, body: list[str]) -> int:
    """Rows on the screen: a blank, the heading, then each line wrapped."""
    width = COLUMNS - 1 - len(output.MARGIN)
    return 2 + len(output._wrap(f"-- {title}", width)) - 1 + sum(
        len(output._wrap(prompts.BULLET + line, width)) for line in body)


class SectionHeightTests(unittest.TestCase):
    def test_every_section_fits_half_the_shortest_window(self):
        measured = 0
        for where, title, body in sections():
            if body is None:
                continue
            measured += 1
            with self.subTest(where=where, title=title):
                self.assertLessEqual(rows(title, list(body)), MOST_ROWS,
                                     f"{where}: '{title}' is {rows(title, list(body))} rows in "
                                     f"{COLUMNS} columns; at most {MOST_ROWS}")
        # The reading of the code still finds them (it would pass, finding none).
        self.assertGreater(measured, 30)

    def test_a_section_is_measured_as_it_wraps(self):
        self.assertEqual(rows("A heading", ["A line.", "x " * 60]), 2 + 1 + 2)


if __name__ == "__main__":
    unittest.main()
