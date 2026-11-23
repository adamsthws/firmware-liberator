"""The rules the tool writes into Tasmota's rule sets: each feature's known by
any text a release wrote, written where it is already or in a free set, and
never over a rule the feature did not write."""

import unittest
from unittest import mock

from tools.autoflash import tasmota_rules
# Registers the features whose rules a release wrote (RELEASED).
from tools.autoflash.routes.espressif import tasmota  # noqa: F401


class FakeRuleSets:
    """Tasmota's answers to its rule set commands, as 16.0.0 gives them
    (xdrv_10_rules.ino): a feature's own fake Tasmota adds the rest."""

    def __init__(self, takes=True):
        self.rules = {name: {"State": "OFF", "Once": "OFF", "StopOnError": "OFF", "Rules": ""}
                      for name in tasmota_rules.RULE_SETS}
        self.sent, self.takes = [], takes

    def held(self, name):
        """A rule set's state and text."""
        return self.rules[name]["State"], self.rules[name]["Rules"]

    def answer(self, name, value):
        """The answer to anything but a rule set's command: none here."""
        return {"Command": "Unknown"}

    def command(self, text, timeout=None):
        self.sent.append(text)
        if text.startswith("Backlog "):
            for part in text[len("Backlog "):].split("; "):
                self.command(part)
            return {}
        name, _, value = text.partition(" ")
        if name not in self.rules:
            return self.answer(name, value)
        rule = self.rules[name]
        if value and self.takes:
            # Rule<x> 0/1 its State, 4/5 Once, 8/9 StopOnError, off/on;
            # " clears its text, which anything else replaces.
            flags = {"0": "State", "1": "State", "4": "Once", "5": "Once", "8": "StopOnError", "9": "StopOnError"}
            if value in flags:
                rule[flags[value]] = "ON" if int(value) % 2 else "OFF"
            else:
                rule["Rules"] = "" if value == '"' else value
        return {name: {**{key: rule[key] for key in ("State", "Once", "StopOnError")},
                       "Length": len(rule["Rules"]), "Free": 511 - len(rule["Rules"]), "Rules": rule["Rules"]}}


# A rule of the owner's.
THEIRS = "ON Power1#State DO Publish stat/blind/power %value% ENDON"

# Every rule text a release has written, by feature and key: a release's new
# text is added here, none ever taken out (tasmota_rules.RULE_TEXTS).
RELEASED = {
    "motor": {
        # 3.2.0; keyed by motor.SWITCH_MODES: 2 push-buttons, 1 one push-button.
        2: ("ON Switch1#State=2 DO ShutterStopOpen1 ENDON ON Switch2#State=2 DO ShutterStopClose1 ENDON",),
        1: ("ON Switch1#State=2 DO ShutterStopToggleDir1 ENDON ON Switch2#State=2 DO ShutterStopToggleDir1 ENDON",),
    },
}

# A second feature of the tool's, beside the motor's, for these tests alone:
# its rule "on" an earlier release's text and the one written now.
DUMMY, DUMMY_EARLIER, DUMMY_RULE = "dummy", "ON Power1#Boot DO Var9 1 ENDON", "ON Power1#Boot DO Var9 2 ENDON"


def dummy():
    """The dummy feature registered, for a test's length."""
    patched = mock.patch.dict(tasmota_rules.RULE_TEXTS)
    patched.start()
    tasmota_rules.register(DUMMY, {"on": (DUMMY_EARLIER, DUMMY_RULE)})
    return patched


def rules(**held):
    """Rule sets holding `held` (name: text, or None for one that did not
    answer), each on; the rest free."""
    return {name: None if held.get(name, "") is None else
            {"State": "ON" if held.get(name) else "OFF", "Once": "OFF", "StopOnError": "OFF",
             "Rules": held.get(name, "")} for name in tasmota_rules.RULE_SETS}


class RegistryTests(unittest.TestCase):
    def test_every_rule_text_a_release_wrote_is_still_the_tools(self):
        # A rule's text changed in place, not added to RULE_TEXTS, leaves a
        # device's earlier one counted as the owner's, running beside the new.
        for feature, keyed in RELEASED.items():
            for key, texts in keyed.items():
                self.assertEqual(tasmota_rules.RULE_TEXTS[feature][key][:len(texts)], texts)
                for text in texts:
                    with self.subTest(feature=feature, key=key, text=text):
                        self.assertEqual(tasmota_rules.whose({"Rules": text}), (feature, key))
                        self.assertEqual(tasmota_rules.own(feature, {"Rules": text}), key)

    def test_the_last_text_is_written_and_earlier_ones_are_stale(self):
        patched = dummy()
        self.addCleanup(patched.stop)
        self.assertEqual(tasmota_rules.written(DUMMY, "on"), DUMMY_RULE)
        self.assertTrue(tasmota_rules.stale({"Rules": DUMMY_EARLIER}))
        self.assertTrue(tasmota_rules.stale({"Rules": DUMMY_EARLIER}, DUMMY))
        self.assertFalse(tasmota_rules.stale({"Rules": DUMMY_EARLIER}, "motor"))
        self.assertFalse(tasmota_rules.stale({"Rules": DUMMY_RULE}))
        self.assertFalse(tasmota_rules.stale({"Rules": THEIRS}))
        self.assertTrue(tasmota_rules.holds({"State": "ON", "Rules": DUMMY_EARLIER}).startswith("an earlier release's"))
        self.assertTrue(tasmota_rules.holds({"State": "ON", "Rules": DUMMY_RULE}).startswith("the tool's rule"))
        self.assertTrue(tasmota_rules.holds({"State": "ON", "Rules": THEIRS}).startswith("another rule"))
        self.assertEqual(tasmota_rules.holds(None), "did not answer")

    def test_a_text_is_known_case_and_spacing_aside(self):
        patched = dummy()
        self.addCleanup(patched.stop)
        self.assertEqual(tasmota_rules.own(DUMMY, {"Rules": "on  power1#boot do var9 2 endon"}), "on")

    def test_no_text_may_be_two_rules(self):
        patched = dummy()
        self.addCleanup(patched.stop)
        for feature, texts in ((DUMMY, {"off": ("ON X DO Y ENDON",)}),
                               ("other", {"on": (DUMMY_RULE.lower(),)}),
                               ("other", {"a": ("ON X DO Y ENDON",), "b": ("ON X DO Y ENDON",)})):
            with self.subTest(feature=feature, texts=texts), self.assertRaises(ValueError):
                tasmota_rules.register(feature, texts)
        self.assertNotIn("other", tasmota_rules.RULE_TEXTS)


class ReadTests(unittest.TestCase):
    def test_each_set_as_it_answers_and_none_for_one_that_does_not(self):
        device = FakeRuleSets()
        device.command(f"Backlog Rule2 {THEIRS}; Rule2 1")
        answer = device.command
        device.command = lambda text, timeout=None: {} if text == "Rule3" else answer(text, timeout)
        found = tasmota_rules.read(device)
        self.assertEqual(found["Rule2"]["Rules"], THEIRS)
        self.assertEqual(found["Rule2"]["State"], "ON")
        self.assertTrue(tasmota_rules.free(found["Rule1"]))
        self.assertIsNone(found["Rule3"])
        self.assertFalse(tasmota_rules.free(None))


class CommandsTests(unittest.TestCase):
    def setUp(self):
        patched = dummy()
        self.addCleanup(patched.stop)

    def test_into_the_first_free_set_on_once_and_stop_on_error_off(self):
        self.assertEqual(tasmota_rules.write_commands(DUMMY, "on", rules(Rule1=THEIRS)),
                         [f"Rule2 {DUMMY_RULE}", "Rule2 4", "Rule2 8", "Rule2 1"])

    def test_where_the_features_rule_is_already_and_cleared_from_any_other(self):
        self.assertEqual(tasmota_rules.write_commands(DUMMY, "on", rules(Rule2=DUMMY_EARLIER, Rule3=DUMMY_RULE)),
                         [f"Rule2 {DUMMY_RULE}", "Rule2 4", "Rule2 8", "Rule2 1", 'Rule3 "', "Rule3 0"])

    def test_never_over_the_owners_nor_into_a_set_that_did_not_answer(self):
        self.assertIsNone(tasmota_rules.write_commands(DUMMY, "on", rules(Rule1=THEIRS, Rule2=None, Rule3=THEIRS)))
        self.assertEqual(tasmota_rules.clear_commands(DUMMY, rules(Rule1=THEIRS, Rule2=None)), [])

    def test_clears_the_features_rule_alone(self):
        self.assertEqual(tasmota_rules.clear_commands(DUMMY, rules(Rule1=THEIRS, Rule3=DUMMY_EARLIER)),
                         ['Rule3 "', "Rule3 0"])

    def test_two_features_never_claim_or_clear_each_others_rule(self):
        motors = RELEASED["motor"][2][-1]
        # The motor's rule counts as not free for the dummy, and is left.
        held = rules(Rule1=motors, Rule2=THEIRS)
        self.assertEqual(tasmota_rules.write_commands(DUMMY, "on", held),
                         [f"Rule3 {DUMMY_RULE}", "Rule3 4", "Rule3 8", "Rule3 1"])
        self.assertEqual(tasmota_rules.clear_commands(DUMMY, held), [])
        self.assertIsNone(tasmota_rules.write_commands(DUMMY, "on", rules(Rule1=motors, Rule2=THEIRS, Rule3=motors)))
        # And the dummy's for the motor, earlier release's text or not.
        for text in (DUMMY_RULE, DUMMY_EARLIER):
            with self.subTest(text):
                held = rules(Rule1=text, Rule2=THEIRS)
                self.assertEqual(tasmota_rules.write_commands("motor", 2, held),
                                 [f"Rule3 {motors}", "Rule3 4", "Rule3 8", "Rule3 1"])
                self.assertEqual(tasmota_rules.clear_commands("motor", held), [])
                self.assertIsNone(tasmota_rules.own("motor", held["Rule1"]))

    def test_written_to_a_fake_tasmota_the_owners_and_the_others_stay(self):
        device = FakeRuleSets()
        device.command(f"Backlog Rule1 {THEIRS}; Rule1 1; Rule2 {RELEASED['motor'][1][-1]}; Rule2 1; Rule3 5")
        device.command("Backlog " + "; ".join(tasmota_rules.write_commands(DUMMY, "on", tasmota_rules.read(device))))
        self.assertEqual(device.held("Rule1"), ("ON", THEIRS))
        self.assertEqual(device.held("Rule2"), ("ON", RELEASED["motor"][1][-1]))
        self.assertEqual(device.held("Rule3"), ("ON", DUMMY_RULE))
        self.assertEqual(device.rules["Rule3"]["Once"], "OFF")
        device.command("Backlog " + "; ".join(tasmota_rules.clear_commands(DUMMY, tasmota_rules.read(device))))
        self.assertEqual(device.held("Rule3"), ("OFF", ""))
        self.assertEqual(device.held("Rule2"), ("ON", RELEASED["motor"][1][-1]))


if __name__ == "__main__":
    unittest.main()
