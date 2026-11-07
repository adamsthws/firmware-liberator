"""A plug on Tasmota, or its startup firmware, as tools/autoflash/device.py
asks it things."""
import unittest
from unittest import mock



class BerryFireTests(unittest.TestCase):
    """A phase that restarts the plug cuts its own reply off: that is no reply,
    not a crash (the E3 traceback on macOS)."""

    def test_a_reply_cut_off_is_no_reply(self):
        import http.client
        from tools.autoflash.device import Device

        device = Device("10.0.0.63")
        for error in (http.client.IncompleteRead(b""), ConnectionResetError(54, "reset")):
            with mock.patch.object(device, "berry", side_effect=error):
                self.assertFalse(device.berry_fire('autoflash_commit("x")'))

    def test_other_errors_still_surface(self):
        from tools.autoflash.device import Device, DeviceError

        device = Device("10.0.0.63")
        with mock.patch.object(device, "berry", side_effect=DeviceError("password")):
            with self.assertRaises(DeviceError):
                device.berry_fire("x")


class RebootDetectionTests(unittest.TestCase):
    """A restart soon after another must still be seen - the D4 hang."""

    class Plug:
        """Answers like a plug that reboots once, after `polls_until_reboot`."""

        def __init__(self, uptime_before, uptime_after, reboots=True):
            from tools.autoflash.device import Device

            outer = self

            class Fake(Device):
                def reachable(self, timeout=3.0):
                    return True

                def started_at(self):
                    return outer.marker()

                def uptime_seconds(self):
                    return outer.uptime()

            self.device = Fake("10.0.0.5")
            self.polls = 0
            self.reboots = reboots
            self.uptime_before = uptime_before
            self.uptime_after = uptime_after

        def rebooted(self):
            self.polls += 1
            return self.reboots and self.polls > 4

        def marker(self):
            return "2026-10-06T13:26:18#3" if self.rebooted() else "2026-10-06T13:25:45#2"

        def uptime(self):
            return self.uptime_after if self.polls > 4 and self.reboots else self.uptime_before + self.polls

    def wait(self, plug, **kwargs):
        import tools.autoflash.device as device_module
        with mock.patch.object(device_module.time, "sleep"):
            return plug.device.wait_for_reboot(timeout=0.5, settle=0, **kwargs)

    def test_a_restart_with_higher_uptime_than_before_is_seen(self):
        # D1 had just restarted the plug: uptime 15s before the upload, and by
        # the time it was back on Wi-Fi after the next restart, 40s.
        plug = self.Plug(uptime_before=15, uptime_after=40)
        self.wait(plug, started_at="2026-10-06T13:25:45#2", before=15)

    def test_no_restart_is_not_mistaken_for_one_just_because_uptime_is_low(self):
        from tools.autoflash.device import DeviceError
        plug = self.Plug(uptime_before=15, uptime_after=0, reboots=False)
        with self.assertRaises(DeviceError):
            self.wait(plug, started_at="2026-10-06T13:25:45#2")

    class Clock:
        """Time that passes only as the code under test sleeps."""

        def __init__(self):
            self.now = 1000.0

        def monotonic(self):
            return self.now

        def sleep(self, seconds):
            self.now += seconds

    def wait_on_clock(self, device, clock, **kwargs):
        import tools.autoflash.device as device_module
        with mock.patch.object(device_module, "time", clock):
            return device.wait_for_reboot(timeout=60, settle=0, **kwargs)

    def test_a_restart_answered_late_on_a_plug_with_no_clock_is_seen(self):
        # F4's Reset 4 at 10s of uptime, just as Tasmota counted the boot: with
        # no clock both boots read "#1", and the request the restart cut off
        # held the first answer back until the new boot was 14s up.
        from tools.autoflash.device import Device
        clock = self.Clock()
        start, restart = clock.now, clock.now + 3.2

        class Fake(Device):
            def reachable(self, timeout=3.0):
                return not restart <= clock.now < restart + 12

            def started_at(self):
                return "#1"

            def uptime_seconds(self):
                if clock.now < restart:
                    return 10 + int(clock.now - start)
                return int(clock.now - restart)

        self.wait_on_clock(Fake("10.0.0.5"), clock, started_at="#1", before=10)

    def test_a_plug_with_no_clock_that_does_not_restart_is_still_waited_for(self):
        from tools.autoflash.device import Device, DeviceError
        clock = self.Clock()
        start = clock.now

        class Fake(Device):
            def reachable(self, timeout=3.0):
                return True

            def started_at(self):
                return "#1"

            def uptime_seconds(self):
                return 10 + int(clock.now - start)

        with self.assertRaises(DeviceError):
            self.wait_on_clock(Fake("10.0.0.5"), clock, started_at="#1", before=10)

    def test_an_uptime_question_the_restart_cuts_off_is_asked_again(self):
        import urllib.error
        from tools.autoflash.device import Device
        clock = self.Clock()
        answers = iter([urllib.error.URLError("connection reset"), 3])

        class Fake(Device):
            def reachable(self, timeout=3.0):
                return True

            def uptime_seconds(self):
                answer = next(answers)
                if isinstance(answer, Exception):
                    raise answer
                return answer

        self.wait_on_clock(Fake("10.0.0.5"), clock, before=10)


class AtNextSaveTests(unittest.TestCase):
    """The Berry driver that puts commands in as Tasmota saves before a restart."""

    def test_the_commands_go_in_a_driver_for_save_before_restart(self):
        from tools.autoflash.device import Device
        plug = Device("192.0.2.1")
        sent = []
        plug.command = lambda c, timeout=None: sent.append(c) or {"Br": "nil" if len(sent) == 1 else "AutoflashAtSave"}
        self.assertTrue(plug.at_next_save(["Hostname x", 'Template {"NAME":"S60"}']))
        self.assertEqual(sent[1], "Br classname(autoflash_at_save)")
        self.assertTrue(sent[0].startswith("Br import global class AutoflashAtSave"))
        self.assertIn("def save_before_restart()", sent[0])
        self.assertIn("""['Hostname x','Template {"NAME":"S60"}']""", sent[0])

    def test_a_firmware_without_berry_says_no(self):
        from tools.autoflash.device import Device
        plug = Device("192.0.2.1")
        plug.command = lambda c, timeout=None: {"Command": "Unknown"}
        self.assertFalse(plug.at_next_save(["Hostname x"]))
        # And Berry that would not take it: its error, not the class, comes back.
        plug.command = lambda c, timeout=None: {"Br": "[syntax_error] input:1: strict: ..."}
        self.assertFalse(plug.at_next_save(["Hostname x"]))

    def test_a_quote_cannot_reach_the_berry(self):
        from tools.autoflash.device import Device
        with self.assertRaises(ValueError):
            Device("192.0.2.1").at_next_save(["Hostname it's"])

    def test_it_runs_once_and_takes_no_command_that_saves(self):
        # SaveData saves the settings itself (SettingsSaveAll), which calls
        # the driver again: in the emulator, again and again until Tasmota's
        # stack overflowed and it restarted unsaved, its settings lost.
        from tools.autoflash.device import Device
        plug = Device("192.0.2.1")
        sent = []
        plug.command = lambda c, timeout=None: sent.append(c) or {"Br": "AutoflashAtSave"}
        plug.at_next_save(["Hostname x"])
        self.assertIn("if self.done return end self.done=true", sent[0])
        for command in ("SaveData 1", "Backlog Hostname x; Module 0", "Restart 1", "Reset 4"):
            with self.assertRaises(ValueError):
                plug.at_next_save([command])


if __name__ == "__main__":
    unittest.main()
