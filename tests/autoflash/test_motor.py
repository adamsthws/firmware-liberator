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
        self.sent, self.takes = [], takes

    def command(self, text, timeout=None):
        self.sent.append(text)
        if text.startswith("Backlog "):
            for part in text[len("Backlog "):].split("; "):
                self.command(part)
            return {}
        name, _, value = text.partition(" ")
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
                 ("switch", "Reverse direction"): True}
        with mock.patch.object(motor.esphome, "cover", return_value={"state": "OPEN"}), \
                mock.patch.object(motor.esphome, "entity", side_effect=lambda host, domain, name: found[domain, name]), \
                mock.patch.object(motor.output, "log_only"):
            motor.note_running(run, "esphome")
        self.assertEqual(run.state.input("motor_running"), "esphome")
        self.assertEqual(run.state.input("motor_setup"), {"open": 12.5, "close": 11.0, "reversed": True})

    def test_tasmotas_travel_times_and_template_are_kept(self):
        device = FakeTasmota()
        device.command("Backlog " + "; ".join(motor.TASMOTA_COMMANDS) + "; ShutterOpenDuration1 7.5")
        device.command("Template " + motor.tasmota_template(kinds.find("sonoff-dualr3"), True))
        run = ctx(device=device)
        with mock.patch.object(motor.output, "log_only"):
            motor.note_running(run, "tasmota")
        self.assertEqual(run.state.input("motor_setup"), {"open": 7.5, "close": 10.0, "reversed": True})

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

    def test_sonoffs_calibrated_travel_and_reversed_outputs_are_carried_across(self):
        device = FakeTasmota()
        result, tell = self.set_up(device, backup={"motorCalibrate": True, "calibrate_time": 8000,
                                                   "motorDriverReverse": True})
        self.assertEqual(result, {"motor": "set up"})
        self.assertEqual(device.travel, {"ShutterOpenDuration1": 8.0, "ShutterCloseDuration1": 8.0})
        # Relay 1, which opens, on output 2 (GPIO14), relay 2 on output 1 (GPIO27).
        gpio = device.template["GPIO"]
        self.assertEqual((gpio[10], gpio[23]), (motor.TASMOTA_RELAY1, motor.TASMOTA_RELAY2))
        self.assertIs(motor.tasmota_reversed(device, kinds.find("sonoff-dualr3")), True)
        self.assertIn("8 s to open, 8 s to close", tell.call_args_list[-1].args[0])

    def test_the_firmware_it_ran_comes_before_the_backup(self):
        device = FakeTasmota()
        self.set_up(device, backup={"motorCalibrate": True, "calibrate_time": 8000, "motorDriverReverse": True},
                    setup={"open": 21.5, "close": 20.0, "reversed": False})
        self.assertEqual(device.travel, {"ShutterOpenDuration1": 21.5, "ShutterCloseDuration1": 20.0})
        self.assertIs(motor.tasmota_reversed(device, kinds.find("sonoff-dualr3")), False)

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
        device.command("Backlog " + "; ".join(motor.TASMOTA_COMMANDS))
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

    def test_an_uncalibrated_or_insane_time_is_not_taken(self):
        for settings in ({"motorCalibrate": False, "calibrate_time": 8000}, {"motorCalibrate": True, "calibrate_time": 0},
                         {"motorCalibrate": True, "calibrate_time": 900_000}, {"motorCalibrate": True},
                         {"motorCalibrate": "true", "calibrate_time": 8000}, {}):
            with self.subTest(settings):
                self.assertFalse(motor.sonoff_setup(settings).timed())

    def test_nothing_known_is_the_default(self):
        with mock.patch.object(motor, "backup_settings", return_value={"deviceMode": 2}):
            self.assertEqual(motor.motor_setup(ctx()), motor.Setup(motor.DEFAULT_TRAVEL, motor.DEFAULT_TRAVEL, False))

    def test_what_the_firmware_did_not_say_comes_from_the_backup(self):
        with mock.patch.object(motor, "backup_settings", return_value={"motorDriverReverse": True}):
            self.assertEqual(motor.motor_setup(ctx(setup={"open": 5.0, "close": 6.0, "reversed": None})),
                             motor.Setup(5.0, 6.0, True))


class SetUpEsphomeTests(unittest.TestCase):
    def test_the_build_is_given_the_motors_setup_and_read_back(self):
        given = {}
        with mock.patch.object(motor, "backup_settings", return_value={"motorCalibrate": True,
                                                                        "calibrate_time": 8000,
                                                                        "motorDriverReverse": True}), \
                mock.patch.object(motor.esphome, "set_number", side_effect=lambda h, n, v: given.__setitem__(n, v)), \
                mock.patch.object(motor.esphome, "set_switch", side_effect=lambda h, n, v: given.__setitem__(n, v)), \
                mock.patch.object(motor.esphome, "entity",
                                  side_effect=lambda h, d, n: f"{given[n]:.1f}" if d == "number" else given.get(n)), \
                mock.patch.object(motor.output, "tell"):
            self.assertEqual(motor.set_up_esphome(ctx()), {"motor": "set up"})
        self.assertEqual(given, {"Open duration": 8.0, "Close duration": 8.0, "Reverse direction": True})

    def test_one_that_does_not_take_them_stops_the_step(self):
        with mock.patch.object(motor, "backup_settings", return_value={}), \
                mock.patch.object(motor.esphome, "set_number"), mock.patch.object(motor.esphome, "set_switch"), \
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
