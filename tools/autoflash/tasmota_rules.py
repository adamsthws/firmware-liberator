"""The rules the tool writes into Tasmota's rule sets, for whichever feature
needs one, beside any rule of the owner's.

Tasmota has three rule sets (RULE_SETS), any of which may hold a rule of the
owner's own, and keeps no mark of who wrote one. Tasmota 16.0.0 gives a rule
back as it was given, case and spacing too, beside its State, Once and
StopOnError (seen in the emulator), so the tool knows a rule of its own by its
text alone: each feature registers every text it has ever written
(RULE_TEXTS). A feature's rule goes where that feature's is already, else in
the first set with no rule; a set holding any other rule - the owner's, or
another feature's - is never written over or cleared (write_commands,
clear_commands).
"""

from __future__ import annotations

from typing import Any, Hashable

from tools.autoflash.device import DeviceError

RULE_SETS = ("Rule1", "Rule2", "Rule3")

# Every text a release has written into a rule set: feature -> key (which of
# the feature's rules) -> its texts, oldest first, the last the one written
# now. A feature adds its rule with register(), from its own module, which
# tools/autoflash/routes/espressif/tasmota.py imports, and lists the texts in
# tests/autoflash/test_tasmota_rules.py's RELEASED once released. A changed
# rule is added to its texts, never edited in place: an earlier release's
# text no longer known would count as the owner's, stay on and run beside the
# new one. One found is written over with the one written now (stale).
RULE_TEXTS: dict[str, dict[Hashable, tuple[str, ...]]] = {}


def register(feature: str, texts: dict[Hashable, tuple[str, ...]]) -> None:
    """`feature`'s rules, by key, each with every text a release wrote. No
    text may be another's, as Tasmota compares them (normalised): a rule
    found is then one feature's alone."""
    if feature in RULE_TEXTS:
        raise ValueError(f"Tasmota rules registered twice for {feature}")
    taken = {normalised(text): (other, key) for other, keyed in RULE_TEXTS.items()
             for key, written in keyed.items() for text in written}
    for key, written in texts.items():
        for text in written:
            if taken.setdefault(normalised(text), (feature, key)) != (feature, key):
                raise ValueError(f"{feature}'s rule {text!r} is already {taken[normalised(text)]}'s")
    RULE_TEXTS[feature] = texts


def written(feature: str, key: Hashable) -> str:
    """The text written now for `feature`'s rule `key`."""
    return RULE_TEXTS[feature][key][-1]


def normalised(text: str) -> str:
    """A rule's text as Tasmota's rules compare it: case and spacing aside."""
    return " ".join(text.split()).upper()


def read(device: Any) -> dict[str, dict[str, Any] | None]:
    """Each of a Tasmota's RULE_SETS as it answers for it: its State, Once
    and StopOnError ("ON" or "OFF") and Rules, its text; None for one it did
    not answer for."""
    found: dict[str, dict[str, Any] | None] = {}
    for name in RULE_SETS:
        try:
            answer = device.command(name).get(name)
        except (DeviceError, OSError, AttributeError):
            answer = None
        found[name] = answer if isinstance(answer, dict) else None
    return found


def whose(rule: dict[str, Any] | None) -> tuple[str, Hashable] | None:
    """Which feature's rule, and which key, a rule set holds, on or off, in
    any release's text; None for one holding any other rule, or none."""
    text = normalised(str(rule.get("Rules", ""))) if rule else ""
    return next(((feature, key) for feature, keyed in RULE_TEXTS.items() for key, texts in keyed.items()
                 if any(text == normalised(each) for each in texts)), None)


def own(feature: str, rule: dict[str, Any] | None) -> Hashable | None:
    """Which of `feature`'s rules a rule set holds, on or off; None for one
    holding another's, or none."""
    found = whose(rule)
    return found[1] if found and found[0] == feature else None


def stale(rule: dict[str, Any] | None, feature: str | None = None) -> bool:
    """Whether a rule set holds a rule of the tool's - of `feature`'s, or
    any feature's - in an earlier release's text, not the one written now."""
    found = whose(rule)
    return bool(found) and feature in (None, found[0]) \
        and normalised(str(rule.get("Rules", ""))) != normalised(written(*found))


def free(rule: dict[str, Any] | None) -> bool:
    """Whether a rule set holds no rule; one that did not answer is not free."""
    return rule is not None and not str(rule.get("Rules", "")).strip()


def holds(rule: dict[str, Any] | None) -> str:
    """What a rule set holds, as the log says it."""
    if rule is None:
        return "did not answer"
    what = ("nothing" if free(rule) else "an earlier release's rule of the tool's" if stale(rule)
            else "the tool's rule" if whose(rule) else "another rule")
    return f"{what} ({', '.join(f'{key} {rule.get(key)}' for key in ('State', 'Once', 'StopOnError'))})"


def owned(feature: str, rules: dict[str, dict[str, Any] | None]) -> list[str]:
    """The rule sets, of `rules` (read), that hold one of `feature`'s rules."""
    return [name for name, found in rules.items() if own(feature, found) is not None]


def write_commands(feature: str, key: Hashable, rules: dict[str, dict[str, Any] | None]) -> list[str] | None:
    """The commands that give a Tasmota whose rule sets are `rules` (read)
    `feature`'s rule `key`: in the set that holds one of `feature`'s
    already, else in the first free one, that set on, Once and StopOnError
    off (4, 8), which an earlier rule there may have left on; `feature`'s
    rule cleared and off in any other set. None where every set holds
    another rule: none the feature did not write is written over."""
    into = (owned(feature, rules) or [name for name, found in rules.items() if free(found)] or [None])[0]
    if into is None:
        return None
    return [f"{into} {written(feature, key)}", f"{into} 4", f"{into} 8", f"{into} 1",
            *clear_commands(feature, {name: found for name, found in rules.items() if name != into})]


def clear_commands(feature: str, rules: dict[str, dict[str, Any] | None]) -> list[str]:
    """The commands that clear (") and switch off each of `rules`' sets that
    holds one of `feature`'s rules, and no other."""
    return [command for name in owned(feature, rules) for command in (f'{name} "', f"{name} 0")]
