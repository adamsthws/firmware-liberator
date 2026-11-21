"""A Dual R3 that drives a motor, kept so through a conversion: its mode
found in its Sonoff backup or the firmware it runs, and Tasmota set up so that
its two outputs are never on together."""

import json
import unittest
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import kinds, motor
from tools.autoflash.errors import Abort
from tools.espressif import flashmap, inspect_stock_nvs


class FakeTasmota:
    """Tasmota's answers to the commands motor.py gives and asks, as 16.0.0
    gives them (xdrv_27_esp32_shutter.ino, support_command.ino)."""

    def __init__(self, takes=True):
        self.settings = {"SetOption80": "OFF", "Interlock": "OFF", "Groups": "", "ShutterRelay1": 0,
                         "ShutterMode": 0, "PowerOnState": 3}
        self.travel = {"ShutterOpenDuration1": 10.0, "ShutterCloseDuration1": 10.0}
        self.template = json.loads(kinds.find("sonoff-dualr3").tasmota_template())
        # Its switches as a template starts them, toggling their relays, and no rules.
        self.switchmodes = {"SwitchMode1": 0, "SwitchMode2": 0}
        self.rules = {name: {"State": "OFF", "Once": "OFF", "StopOnError": "OFF", "Rules": ""}
                      for name in motor.TASMOTA_RULES}
        self.sent, self.takes = [], takes

    def held(self, name):
        """A rule set's state and text."""
        return self.rules[name]["State"], self.rules[name]["Rules"]

    def command(self, text, timeout=None):
        self.sent.append(text)
        if text.startswith("Backlog "):
            for part in text[len("Backlog "):].split("; "):
                self.command(part)
            return {}
        name, _, value = text.partition(" ")
        if name in self.switchmodes:
            if value and self.takes:
                self.switchmodes[name] = int(value)
            return {name: self.switchmodes[name]}
        if name in self.rules:
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
        if name == "Template":
            if value and self.takes:
                self.template = json.loads(value)
            return self.template
        if name in self.travel:
            if self.settings["SetOption80"] != "ON" or not self.settings["ShutterRelay1"]:
                return {"Command": "Unknown"}
            if value and self.takes:
                self.travel[name] = float(value)
            return {name: self.travel[name]}
        if value and self.takes:
            if name == "Interlock" and "," in value:
                self.settings["Groups"] = value
            elif name == "Interlock":
                self.settings["Interlock"] = "ON" if value == "1" else "OFF"
            elif name == "SetOption80":
                self.settings[name] = "ON" if value == "1" else "OFF"
            elif name == "ShutterRelay1" and self.settings["SetOption80"] != "ON":
                pass
            else:
                self.settings[name] = int(value)
        if name == "Interlock" and self.settings["SetOption80"] == "ON" and not self.settings["ShutterMode"]:
            # Interlock starts the shutters again, which take their mode for good.
            self.settings["ShutterMode"] = 1 if self.settings["Interlock"] == "ON" else 2
        if name == "Interlock":
            return {"Interlock": self.settings["Interlock"], "Groups": self.settings["Groups"]}
        if name == "ShutterRelay1" and self.settings["SetOption80"] != "ON":
            return {"Command": "Unknown"}
        return {name: self.settings[name]}


def switch_commands(mode, device=None):
    """The commands that set `device`'s wall switches to `mode`: by default,
    one whose rule sets are all free."""
    return motor.tasmota_switch_commands(mode, motor.tasmota_rules(device or FakeTasmota()))


# A rule of the person's own.
THEIRS = "ON Power1#State DO Publish stat/blind/power %value% ENDON"


def ctx(kind="sonoff-dualr3", running=None, device=None, setup=None):
    inputs = {"device_kind": kind, "device_mac": "12:34:56:ab:cd:ef", "device_ip": "192.0.2.50",
              "motor_running": running, "motor_setup": setup}
    state = SimpleNamespace(input=inputs.get, set_input=inputs.__setitem__)
    return SimpleNamespace(state=state, device=device or FakeTasmota())


class SonoffSettingsTests(unittest.TestCase):
    """deviceMode in Sonoff's user settings, as its backup holds them."""

    def settings(self, content, namespace="user_usual"):
        value = inspect_stock_nvs.Value(namespace=3, key="content", kind=0x21, chunks={0: content})
        flash = bytes(range(256)) * 0x200
        with mock.patch.object(flashmap, "nvs", return_value=SimpleNamespace(offset=0x9000, size=0x14000)), \
                mock.patch.object(inspect_stock_nvs, "parse",
                                  return_value=({3: namespace}, {(3, "content"): value})) as parse:
            found = motor.sonoff_settings(flash)
        # Its NVS partition, wherever the device's table puts it: the Dual R3's is the TH Elite's.
        self.assertEqual(parse.call_args.args[0], flash[0x9000:0x1D000])
        return found

    def test_the_user_settings_are_read(self):
        self.assertEqual(self.settings(b'{"init":"true","deviceMode":2}\x00\xff'), {"init": "true", "deviceMode": 2})

    def test_none_without_user_settings(self):
        self.assertIsNone(self.settings(b'{"deviceMode":2}\x00', namespace="user_unusual"))

    def test_relay_mode_alone_is_relays(self):
        self.assertIs(motor.sonoff_motor({"deviceMode": 1}), False)
        self.assertIs(motor.sonoff_motor({"deviceMode": "1"}), False)

    def test_every_other_mode_is_a_motor_as_sonoffs_firmware_runs_it(self):
        # It writes 2 over none, or one outside 1 to 3; 3 is not known.
        for settings in ({"deviceMode": 2}, {"deviceMode": 3}, {"deviceMode": 7}, {}):
            with self.subTest(settings):
                self.assertIs(motor.sonoff_motor(settings), True)

    def test_no_settings_say_nothing(self):
        self.assertIsNone(motor.sonoff_motor(None))


class DrivesTests(unittest.TestCase):
    def test_a_backup_in_motor_mode_drives_a_motor(self):
        with mock.patch.object(motor, "backup_settings", return_value={"deviceMode": 2}):
            self.assertTrue(motor.drives(ctx()))

    def test_a_backup_in_relay_mode_does_not(self):
        with mock.patch.object(motor, "backup_settings", return_value={"deviceMode": 1}):
            self.assertFalse(motor.drives(ctx()))

    def test_the_firmware_it_ran_set_up_for_a_motor_does_whatever_the_backup(self):
        for backup in (None, {"deviceMode": 1}):
            with self.subTest(backup), mock.patch.object(motor, "backup_settings", return_value=backup):
                self.assertTrue(motor.drives(ctx(running="tasmota")))

    def test_no_backup_and_relays_is_relays(self):
        with mock.patch.object(motor, "backup_settings", return_value=None):
            self.assertFalse(motor.drives(ctx()))

    def test_a_kind_with_no_motor_mode_never_does(self):
        with mock.patch.object(motor, "backup_settings", return_value={"deviceMode": 2}):
            self.assertFalse(motor.drives(ctx(kind="sonoff-s60", running="tasmota")))


class NoteRunningTests(unittest.TestCase):
    """The firmware the device runs as the run begins, asked before it leaves it."""

    def test_tasmota_set_up_for_a_motor_is_kept(self):
        device = FakeTasmota()
        device.command("Backlog " + "; ".join(motor.TASMOTA_COMMANDS))
        run = ctx(device=device)
        with mock.patch.object(motor.output, "log_only"):
            motor.note_running(run, "tasmota")
        self.assertEqual(run.state.input("motor_running"), "tasmota")

    def test_tasmota_interlocked_by_hand_counts_as_a_motors(self):
        device = FakeTasmota()
        device.command("Backlog Interlock 1,2; Interlock 1")
        self.assertTrue(motor.tasmota_motor(device))

    def test_tasmota_with_two_free_relays_is_not_kept(self):
        run = ctx()
        with mock.patch.object(motor.output, "log_only"):
            motor.note_running(run, "tasmota")
        self.assertIsNone(run.state.input("motor_running"))

    def test_an_esphome_with_a_cover_is_kept_with_its_settings(self):
        run = ctx()
        # As ESPHome 2026.10.0's web server gives them (seen in the emulator): a number's value as text.
        found = {("number", "Open duration"): "12.5", ("number", "Close duration"): "11.0",
                 ("switch", "Reverse direction"): True, ("select", "Wall switches"): "Hold to run",
                 ("switch", "Swap wall switches"): True}
        with mock.patch.object(motor.esphome, "cover", return_value={"state": "OPEN"}), \
                mock.patch.object(motor.esphome, "entity", side_effect=lambda host, domain, name: found[domain, name]), \
                mock.patch.object(motor.output, "log_only"):
            motor.note_running(run, "esphome")
        self.assertEqual(run.state.input("motor_running"), "esphome")
        self.assertEqual(run.state.input("motor_setup"), {"open": 12.5, "close": 11.0, "reversed": True,
                                                         "switches": motor.HOLD_TO_RUN, "swapped": True})
        self.assertEqual(motor.motor_setup(run), motor.Setup(12.5, 11.0, True, motor.HOLD_TO_RUN, True))

    def test_tasmotas_travel_times_template_and_switches_are_kept(self):
        device = FakeTasmota()
        device.command("Backlog " + "; ".join([*motor.TASMOTA_COMMANDS, "ShutterOpenDuration1 7.5",
                                               *switch_commands(motor.ONE_BUTTON)]))
        device.command("Template " + motor.tasmota_template(kinds.find("sonoff-dualr3"), True, True))
        run = ctx(device=device)
        with mock.patch.object(motor.output, "log_only"):
            motor.note_running(run, "tasmota")
        self.assertEqual(run.state.input("motor_setup"), {"open": 7.5, "close": 10.0, "reversed": True,
                                                         "switches": motor.ONE_BUTTON, "swapped": True})

    def test_tasmotas_one_push_button_goes_by_the_way_it_last_moved(self):
        # Seen in the emulator: ShutterStopToggle1 goes by where the shutter
        # is, and opened again a motor stopped part way up.
        rule = motor.TASMOTA_SWITCHES[motor.ONE_BUTTON][1]
        self.assertEqual(rule.count("DO ShutterStopToggleDir1 ENDON"), 2)
        self.assertNotIn("ShutterStopToggle1", rule)

    def test_tasmotas_switches_are_read_as_each_mode_and_none_set_another_way(self):
        for mode in motor.SWITCH_MODES:
            with self.subTest(mode):
                device = FakeTasmota()
                device.command("Backlog " + "; ".join(switch_commands(mode)))
                self.assertEqual(motor.tasmota_switches(device), mode)
        # As Tasmota could give the rule back, its case and spacing its own.
        device = FakeTasmota()
        device.command("Backlog " + "; ".join(switch_commands(motor.PUSH_BUTTONS)))
        device.rules["Rule1"]["Rules"] = "on  switch1#state=2 do shutterstopopen1 endon " \
                                         "on switch2#state=2 do shutterstopclose1 endon"
        self.assertEqual(motor.tasmota_switches(device), motor.PUSH_BUTTONS)
        # Switches toggling their relays, as a template starts them and an earlier release left them.
        self.assertIsNone(motor.tasmota_switches(FakeTasmota()))
        # A rule switched off, or run once, is no rule; switches unlike each other are none of them.
        for off in ("Rule1 0", "Rule1 5"):
            with self.subTest(off):
                device = FakeTasmota()
                device.command("Backlog " + "; ".join(switch_commands(motor.PUSH_BUTTONS)) + f"; {off}")
                self.assertIsNone(motor.tasmota_switches(device))
        device = FakeTasmota()
        device.command("Backlog " + "; ".join(switch_commands(motor.HOLD_TO_RUN)) + "; SwitchMode2 1")
        self.assertIsNone(motor.tasmota_switches(device))

    def test_tasmotas_switches_are_read_with_the_rule_in_any_rule_set(self):
        for name in motor.TASMOTA_RULES:
            with self.subTest(name):
                device = FakeTasmota()
                device.command(f"Backlog SwitchMode1 4; SwitchMode2 4; {name} "
                               f"{motor.TASMOTA_SWITCHES[motor.ONE_BUTTON][1]}; {name} 1")
                # The person's own rules beside it, whatever they do, say nothing of the switches.
                for other in set(motor.TASMOTA_RULES) - {name}:
                    device.command(f"Backlog {other} {THEIRS}; {other} 1")
                self.assertEqual(motor.tasmota_switches(device), motor.ONE_BUTTON)
                run = ctx(device=device)
                device.command("Backlog " + "; ".join(motor.TASMOTA_COMMANDS))
                with mock.patch.object(motor.output, "log_only"):
                    motor.note_running(run, "tasmota")
                self.assertEqual(run.state.input("motor_setup")["switches"], motor.ONE_BUTTON)
        # Hold to run beside the person's rules; not with the tool's on in two sets, which it never leaves.
        device = FakeTasmota()
        device.command(f"Backlog SwitchMode1 2; SwitchMode2 2; Rule2 {THEIRS}; Rule2 1")
        self.assertEqual(motor.tasmota_switches(device), motor.HOLD_TO_RUN)
        device = FakeTasmota()
        device.command("Backlog SwitchMode1 4; SwitchMode2 4; "
                       + "; ".join(f"{name} {motor.TASMOTA_SWITCHES[mode][1]}; {name} 1"
                                   for name, mode in (("Rule1", motor.ONE_BUTTON), ("Rule3", motor.PUSH_BUTTONS))))
        self.assertIsNone(motor.tasmota_switches(device))

    def test_a_rule_set_that_does_not_answer_is_neither_read_nor_written(self):
        device = FakeTasmota()
        device.command("Backlog " + "; ".join(switch_commands(motor.HOLD_TO_RUN)))
        answer = device.command
        device.command = lambda text, timeout=None: {} if text == "Rule1" else answer(text, timeout)
        self.assertIsNone(motor.tasmota_switches(device))
        self.assertEqual(switch_commands(motor.PUSH_BUTTONS, device)[2],
                         f"Rule2 {motor.TASMOTA_SWITCHES[motor.PUSH_BUTTONS][1]}")

    def test_an_esphome_without_one_is_relays_and_one_silent_says_nothing(self):
        with mock.patch.object(motor.esphome, "cover", return_value=None):
            with mock.patch.object(motor.esphome, "is_esphome", return_value=True):
                self.assertIs(motor.esphome_motor("192.0.2.50"), False)
            with mock.patch.object(motor.esphome, "is_esphome", return_value=False):
                self.assertIsNone(motor.esphome_motor("192.0.2.50"))

    def test_another_kind_is_not_asked(self):
        device = mock.Mock()
        motor.note_running(ctx(kind="sonoff-s60", device=device), "tasmota")
        device.command.assert_not_called()


class SetUpTasmotaTests(unittest.TestCase):
    """Tasmota's shutter on both relays, interlocked, both off at power-on."""

    def set_up(self, device, mode=2, backup=None, setup=None):
        run = ctx(device=device, setup=setup)
        settings = {"deviceMode": mode, **(backup or {})}

        def give_template(device, template, hostname, named=False):
            device.command(f"Template {template}")
        with mock.patch.object(motor, "backup_settings", return_value=settings), \
                mock.patch("tools.autoflash.routes.espressif.tasmota.give_template", side_effect=give_template), \
                mock.patch.object(motor.output, "tell") as tell, mock.patch.object(motor.output, "say"), \
                mock.patch.object(motor.output, "log_only"), mock.patch.object(motor.time, "sleep"):
            return motor.set_up_tasmota(run), tell

    def test_a_motors_tasmota_is_given_every_setting_and_the_person_told_the_travel_times(self):
        device = FakeTasmota()
        result, tell = self.set_up(device)
        self.assertEqual(result, {"motor": "set up"})
        self.assertTrue(motor.tasmota_in_force(device))
        self.assertEqual(device.settings, {"SetOption80": "ON", "Interlock": "ON", "Groups": "1,2",
                                           "ShutterRelay1": 1, "ShutterMode": 1, "PowerOnState": 0})
        # Not timed: the short default each way, the person told to time theirs.
        self.assertEqual(device.travel, {"ShutterOpenDuration1": motor.DEFAULT_TRAVEL,
                                         "ShutterCloseDuration1": motor.DEFAULT_TRAVEL})
        self.assertEqual(device.template, json.loads(kinds.find("sonoff-dualr3").tasmota_template()))
        self.assertIn(motor.TASMOTA_TRAVEL, tell.call_args_list[-1].args[0])
        self.assertIn(f"{motor.DEFAULT_TRAVEL:g} s until you do", tell.call_args_list[-1].args[0])
        # Sonoff's default for its wall switches: push-buttons, each acting as it is let go.
        self.assertEqual(device.switchmodes, {"SwitchMode1": 4, "SwitchMode2": 4})
        # A fresh Tasmota's rule sets are all free: the rule in Rule1.
        self.assertEqual(device.held("Rule1"), ("ON", motor.TASMOTA_SWITCHES[motor.PUSH_BUTTONS][1]))
        self.assertEqual([device.held(name) for name in ("Rule2", "Rule3")], [("OFF", "")] * 2)
        self.assertIn("Switches are push-buttons", tell.call_args_list[0].args[0])

    def test_sonoffs_calibrated_travel_and_reversed_outputs_are_carried_across(self):
        device = FakeTasmota()
        result, tell = self.set_up(device, backup={"motorCalibrate": True, "calibrate_time": 8000,
                                                   "motorDriverReverse": True})
        self.assertEqual(result, {"motor": "set up"})
        self.assertEqual(device.travel, {"ShutterOpenDuration1": 8.0, "ShutterCloseDuration1": 8.0})
        # Relay 1, which opens, on output 2 (GPIO14), relay 2 on output 1 (GPIO27).
        gpio = device.template["GPIO"]
        self.assertEqual((gpio[10], gpio[23]), (motor.TASMOTA_RELAY1, motor.TASMOTA_RELAY2))
        self.assertEqual(motor.tasmota_wiring(device, kinds.find("sonoff-dualr3")), (True, False))
        self.assertIn("8 s to open, 8 s to close", tell.call_args_list[-1].args[0])

    def test_sonoffs_wall_switches_are_carried_across_in_each_mode(self):
        for mode in motor.SWITCH_MODES:
            with self.subTest(mode):
                device = FakeTasmota()
                self.set_up(device, backup={"motorSwMode": mode})
                self.assertEqual(motor.tasmota_switches(device), mode)

    def test_swapped_wall_switches_are_swapped_in_the_template(self):
        device = FakeTasmota()
        self.set_up(device, backup={"motorSwMode": motor.HOLD_TO_RUN, "motorKeyReverse": True})
        # Switch 1, which opens, on S2 (GPIO33), switch 2 on S1 (GPIO32); the relays as they were.
        gpio = device.template["GPIO"]
        self.assertEqual((gpio[28], gpio[29]), (motor.TASMOTA_SWITCH2, motor.TASMOTA_SWITCH1))
        self.assertEqual(motor.tasmota_wiring(device, kinds.find("sonoff-dualr3")), (False, True))
        self.assertEqual(device.switchmodes, {"SwitchMode1": 2, "SwitchMode2": 2})
        self.assertEqual(device.held("Rule1"), ("OFF", ""))

    def test_the_firmware_it_ran_comes_before_the_backup(self):
        device = FakeTasmota()
        self.set_up(device, backup={"motorCalibrate": True, "calibrate_time": 8000, "motorDriverReverse": True,
                                    "motorSwMode": 3, "motorKeyReverse": True},
                    setup={"open": 21.5, "close": 20.0, "reversed": False, "switches": 1, "swapped": False})
        self.assertEqual(device.travel, {"ShutterOpenDuration1": 21.5, "ShutterCloseDuration1": 20.0})
        self.assertEqual(motor.tasmota_wiring(device, kinds.find("sonoff-dualr3")), (False, False))
        self.assertEqual(motor.tasmota_switches(device), motor.ONE_BUTTON)

    def with_rules(self, **rules):
        """A Tasmota set up for the motor but its wall switches, its rule sets
        holding `rules` (name: text), each on."""
        device = FakeTasmota()
        device.command("Backlog " + "; ".join([*motor.TASMOTA_COMMANDS, *(f"{name} {text}; {name} 1"
                                                                         for name, text in rules.items())]))
        return device

    def test_the_tools_rule_in_rule2_is_changed_there(self):
        device = self.with_rules(Rule1=THEIRS, Rule2=motor.TASMOTA_SWITCHES[motor.PUSH_BUTTONS][1])
        device.command("Backlog SwitchMode1 4; SwitchMode2 4")
        result, tell = self.set_up(device, backup={"motorSwMode": motor.ONE_BUTTON})
        self.assertEqual(result, {"motor": "set up"})
        self.assertEqual(motor.tasmota_switches(device), motor.ONE_BUTTON)
        self.assertEqual(device.held("Rule2"), ("ON", motor.TASMOTA_SWITCHES[motor.ONE_BUTTON][1]))
        self.assertEqual(device.held("Rule1"), ("ON", THEIRS))
        self.assertEqual(device.held("Rule3"), ("OFF", ""))
        self.assertIn("Switches are one push-button", tell.call_args_list[0].args[0])

    def test_a_rule_of_the_persons_in_rule1_is_kept_and_the_tools_goes_in_the_next_free(self):
        # Theirs on, or switched off and kept for later: Rule1 is not free either way.
        for state in ("1", "0"):
            with self.subTest(state):
                device = self.with_rules(Rule1=THEIRS)
                device.command(f"Rule1 {state}")
                # A free set's Once, left on by a rule cleared from it, put off.
                device.command("Rule2 5")
                self.assertEqual(self.set_up(device)[0], {"motor": "set up"})
                self.assertEqual(device.held("Rule1"), ("ON" if state == "1" else "OFF", THEIRS))
                self.assertEqual(device.held("Rule2"), ("ON", motor.TASMOTA_SWITCHES[motor.PUSH_BUTTONS][1]))
                self.assertEqual(device.rules["Rule2"]["Once"], "OFF")
                self.assertEqual(motor.tasmota_switches(device), motor.PUSH_BUTTONS)

    def test_with_every_rule_set_the_persons_none_is_written_and_the_switches_are_hold_to_run(self):
        theirs = {name: f"{THEIRS} ON Power{n}#State DO Var{n} 1 ENDON" for n, name in enumerate(motor.TASMOTA_RULES, 1)}
        device = self.with_rules(**theirs)
        device.sent.clear()
        result, tell = self.set_up(device, backup={"motorCalibrate": True, "calibrate_time": 8000})
        self.assertEqual(result, {"motor": "set up"})
        self.assertEqual({name: device.held(name) for name in motor.TASMOTA_RULES},
                         {name: ("ON", text) for name, text in theirs.items()})
        self.assertFalse([text for text in device.sent if text.startswith("Rule") and " " in text],
                         "no rule set written")
        # The rest of the motor's setup still given.
        self.assertTrue(motor.tasmota_in_force(device))
        self.assertEqual(device.travel, {"ShutterOpenDuration1": 8.0, "ShutterCloseDuration1": 8.0})
        self.assertEqual(motor.tasmota_switches(device), motor.HOLD_TO_RUN)
        said = [call.args[0] for call in tell.call_args_list]
        self.assertEqual(said, ["    Motor mode carried across: Switches are hold to run, not push-buttons.",
                                "    Its travel times carried across: 8 s to open, 8 s to close."])

    def test_the_person_is_told_in_a_line_how_the_switches_were_carried(self):
        cases = (
            (motor.Setup(8.0, 8.0, False, motor.PUSH_BUTTONS, False), False,
             "Switches are push-buttons."),
            (motor.Setup(8.0, 8.0, True, motor.HOLD_TO_RUN, True), False,
             "Switches are hold to run."),
            (motor.Setup(8.0, 8.0, False, motor.ONE_BUTTON, True), False,
             "Switches are one push-button."),
            (motor.Setup(8.0, 8.0, True, motor.ONE_BUTTON, False), True,
             "Switches are hold to run, not one push-button."),
        )
        for setup, rule_wanted, line in cases:
            with self.subTest(line), mock.patch.object(motor.output, "tell") as tell, \
                    mock.patch.object(motor.output, "log_only"), \
                    mock.patch.object(motor.prompts, "countdown_carry_on", return_value=True):
                motor.tell_set_up(motor.TASMOTA_TRAVEL, setup, True, rule_wanted=rule_wanted)
                self.assertEqual([call.args[0] for call in tell.call_args_list],
                                 [f"    Motor mode carried across: {line}",
                                  "    Its travel times carried across: 8 s to open, 8 s to close."])

    def test_switches_left_hold_to_run_are_said_long_enough_to_read(self):
        # A stage that passes takes what it said off the screen.
        setup = motor.Setup(open=8.0, close=8.0, reversed=False, switches=motor.PUSH_BUTTONS, swapped=False)
        waits = [mock.call("Carrying on", seconds=motor.RULE_WANTED_SHOWN)]
        for rule_wanted, counted in ((True, waits), (False, [])):
            with self.subTest(rule_wanted=rule_wanted), mock.patch.object(motor.output, "tell"), \
                    mock.patch.object(motor.prompts, "countdown_carry_on", return_value=True) as countdown:
                motor.tell_set_up(motor.TASMOTA_TRAVEL, setup, True, rule_wanted=rule_wanted)
                self.assertEqual(countdown.call_args_list, counted)
        # q there stops the run, as at every question.
        with mock.patch.object(motor.output, "tell"), \
                mock.patch.object(motor.prompts, "countdown_carry_on", return_value=False), \
                self.assertRaises(motor.prompts.QuitRun):
            motor.tell_set_up(motor.TASMOTA_TRAVEL, setup, True, rule_wanted=True)

    def test_hold_to_run_clears_the_tools_rule_alone(self):
        device = self.with_rules(Rule1=THEIRS, Rule2=motor.TASMOTA_SWITCHES[motor.ONE_BUTTON][1],
                                 Rule3=THEIRS + " ON Power2#State DO Var2 1 ENDON")
        device.command("Backlog SwitchMode1 4; SwitchMode2 4")
        result, tell = self.set_up(device, backup={"motorSwMode": motor.HOLD_TO_RUN})
        self.assertEqual(result, {"motor": "set up"})
        self.assertEqual(device.held("Rule1"), ("ON", THEIRS))
        self.assertEqual(device.held("Rule2"), ("OFF", ""))
        self.assertEqual(device.held("Rule3"), ("ON", THEIRS + " ON Power2#State DO Var2 1 ENDON"))
        self.assertEqual(motor.tasmota_switches(device), motor.HOLD_TO_RUN)
        self.assertIn("Switches are hold to run", tell.call_args_list[0].args[0])

    def test_one_an_earlier_release_set_up_is_given_its_wall_switches(self):
        # Every setting but the switches, which still toggle their relays.
        device = FakeTasmota()
        device.command("Backlog " + "; ".join(motor.TASMOTA_COMMANDS))
        self.assertEqual(self.set_up(device, backup={"motorSwMode": 2})[0], {"motor": "set up"})
        self.assertEqual(motor.tasmota_switches(device), motor.PUSH_BUTTONS)

    def test_a_template_not_taken_stops_the_step(self):
        with self.assertRaises(Abort):
            self.set_up(FakeTasmota(takes=False), backup={"motorDriverReverse": True})

    def test_the_interlock_is_given_before_shutters_are_on_and_the_mode_after(self):
        # Seen in the emulator: shutters on first, "Interlock 1,2" started them
        # with the relays not yet interlocked, and they took mode 2 - power and
        # direction - for good.
        commands = list(motor.TASMOTA_COMMANDS)
        self.assertLess(commands.index("Interlock 1"), commands.index("SetOption80 1"))
        self.assertLess(commands.index("ShutterRelay1 1"), commands.index("ShutterMode 1"))

    def test_a_shutter_in_mode_two_is_not_in_force_and_is_put_right(self):
        device = FakeTasmota()
        device.command("Backlog " + "; ".join(motor.TASMOTA_COMMANDS))
        device.command("ShutterMode 2")
        self.assertFalse(motor.tasmota_in_force(device))
        self.assertEqual(self.set_up(device)[0], {"motor": "set up"})
        self.assertEqual(device.settings["ShutterMode"], 1)

    def test_one_set_up_already_is_left_as_it_is(self):
        device = FakeTasmota()
        device.command("Backlog " + "; ".join([*motor.TASMOTA_COMMANDS,
                                               *switch_commands(motor.PUSH_BUTTONS)]))
        device.command("ShutterRelay1")
        device.sent.clear()
        result, tell = self.set_up(device)
        self.assertEqual(result, {"motor": "in place"})
        self.assertFalse([text for text in device.sent if " " in text], "nothing set")
        tell.assert_not_called()

    def test_a_tasmota_that_does_not_take_them_stops_the_step(self):
        with mock.patch.object(motor.time, "monotonic", side_effect=[0.0, motor.TASMOTA_TAKES + 1]), \
                self.assertRaises(Abort):
            self.set_up(FakeTasmota(takes=False))

    def test_a_device_in_relay_mode_is_left_alone(self):
        device = FakeTasmota()
        self.assertEqual(self.set_up(device, mode=1)[0], {})
        self.assertEqual(device.sent, [])

    def test_the_power_on_state_alone_missing_is_not_in_force(self):
        device = FakeTasmota()
        device.command("Backlog " + "; ".join(motor.TASMOTA_COMMANDS))
        device.command("PowerOnState 3")
        self.assertFalse(motor.tasmota_in_force(device))
        self.assertTrue(motor.tasmota_motor(device))


class SonoffSetupTests(unittest.TestCase):
    """What Sonoff's own settings set the motor to, as seen in the emulator:
    calibrate_time the travel in ms, once motorCalibrate; motorDriverReverse
    output 2 opening."""

    def test_a_calibrated_motors_travel_and_direction(self):
        self.assertEqual(motor.sonoff_setup({"motorCalibrate": True, "calibrate_time": 12000,
                                             "motorDriverReverse": True}), motor.Setup(12.0, 12.0, True))

    def test_its_wall_switches_mode_and_way_round(self):
        self.assertEqual(motor.sonoff_setup({"motorSwMode": 3, "motorKeyReverse": True}),
                         motor.Setup(switches=motor.HOLD_TO_RUN, swapped=True))
        for settings in ({"motorSwMode": 0}, {"motorSwMode": 4}, {"motorSwMode": "2"}, {"motorSwMode": True},
                         {"motorKeyReverse": 1}):
            with self.subTest(settings):
                self.assertEqual(motor.sonoff_setup(settings), motor.Setup())

    def test_nothing_known_of_the_switches_is_sonoffs_default(self):
        with mock.patch.object(motor, "backup_settings", return_value={"deviceMode": 2}):
            found = motor.motor_setup(ctx())
        self.assertEqual((found.switches, found.swapped), (motor.PUSH_BUTTONS, False))

    def test_an_uncalibrated_or_insane_time_is_not_taken(self):
        for settings in ({"motorCalibrate": False, "calibrate_time": 8000}, {"motorCalibrate": True, "calibrate_time": 0},
                         {"motorCalibrate": True, "calibrate_time": 900_000}, {"motorCalibrate": True},
                         {"motorCalibrate": "true", "calibrate_time": 8000}, {}):
            with self.subTest(settings):
                self.assertFalse(motor.sonoff_setup(settings).timed())

    def test_nothing_known_is_the_default(self):
        with mock.patch.object(motor, "backup_settings", return_value={"deviceMode": 2}):
            self.assertEqual(motor.motor_setup(ctx()), motor.Setup(motor.DEFAULT_TRAVEL, motor.DEFAULT_TRAVEL, False,
                                                                   motor.PUSH_BUTTONS, False))

    def test_what_the_firmware_did_not_say_comes_from_the_backup(self):
        with mock.patch.object(motor, "backup_settings", return_value={"motorDriverReverse": True}):
            self.assertEqual(motor.motor_setup(ctx(setup={"open": 5.0, "close": 6.0, "reversed": None})),
                             motor.Setup(5.0, 6.0, True, motor.PUSH_BUTTONS, False))


class SetUpEsphomeTests(unittest.TestCase):
    def test_the_build_is_given_the_motors_setup_and_read_back(self):
        given = {}
        with mock.patch.object(motor, "backup_settings", return_value={"motorCalibrate": True,
                                                                        "calibrate_time": 8000,
                                                                        "motorDriverReverse": True,
                                                                        "motorSwMode": 1, "motorKeyReverse": True}), \
                mock.patch.object(motor.esphome, "set_number", side_effect=lambda h, n, v: given.__setitem__(n, v)), \
                mock.patch.object(motor.esphome, "set_switch", side_effect=lambda h, n, v: given.__setitem__(n, v)), \
                mock.patch.object(motor.esphome, "set_select", side_effect=lambda h, n, v: given.__setitem__(n, v)), \
                mock.patch.object(motor.esphome, "entity",
                                  side_effect=lambda h, d, n: f"{given[n]:.1f}" if d == "number" else given.get(n)), \
                mock.patch.object(motor.output, "tell"):
            self.assertEqual(motor.set_up_esphome(ctx()), {"motor": "set up"})
        self.assertEqual(given, {"Open duration": 8.0, "Close duration": 8.0, "Reverse direction": True,
                                 "Wall switches": "One push-button", "Swap wall switches": True})

    def test_one_that_does_not_take_them_stops_the_step(self):
        with mock.patch.object(motor, "backup_settings", return_value={}), \
                mock.patch.object(motor.esphome, "set_number"), mock.patch.object(motor.esphome, "set_switch"), \
                mock.patch.object(motor.esphome, "set_select"), \
                mock.patch.object(motor.esphome, "entity", return_value=None), \
                mock.patch.object(motor.time, "sleep"), self.assertRaises(Abort):
            motor.set_up_esphome(ctx())


class InterlockAnswerTests(unittest.TestCase):
    def test_relays_one_and_two_in_one_group_on(self):
        self.assertTrue(motor.interlocked({"Interlock": "ON", "Groups": "1,2"}))
        self.assertTrue(motor.interlocked({"Interlock": "ON", "Groups": "3,4 1,2"}))

    def test_off_or_apart_is_not(self):
        self.assertFalse(motor.interlocked({"Interlock": "OFF", "Groups": "1,2"}))
        self.assertFalse(motor.interlocked({"Interlock": "ON", "Groups": "1,3 2,4"}))
        self.assertFalse(motor.interlocked("Interlock ON"))


class LogTests(unittest.TestCase):
    def test_sonoffs_own_motor_settings_are_logged_for_a_report(self):
        settings = {"deviceMode": 2, "calibrate_time": 1234, "motorDriverReverse": 0, "ssid": "Home"}
        with mock.patch.object(motor, "backup_settings", return_value=settings), \
                mock.patch.object(motor.output, "log_only") as log:
            motor.log_sonoff_motor(ctx())
        said = log.call_args.args[0]
        self.assertIn(json.dumps({"calibrate_time": 1234, "motorDriverReverse": 0}), said)
        self.assertIn("its Sonoff backup", said)


if __name__ == "__main__":
    unittest.main()
