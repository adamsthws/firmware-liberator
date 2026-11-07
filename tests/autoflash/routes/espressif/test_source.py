"""What the plug runs when a run starts: a plug on Tasmota with ESPHome's
bootloader already leaves it for ESPHome without the hub."""
import hashlib
import unittest
from unittest import mock

from tests.autoflash.routes.espressif.test_espressif import RouteTestCase
from tools.autoflash import esphome, routes
from tools.autoflash.device import DeviceError
from tools.autoflash.state import DONE
from tools.autoflash.routes import espressif, update
from tools.autoflash.routes.espressif import source
from tools.espressif import chips


class LeavingTasmotaForESPHomeTests(RouteTestCase):
    """Every plug ends with ESPHome's bootloader: only a plug that has it skips
    the hub, which writes it, and the choice is kept for the rest of the run."""

    def settle(self, *, direct):
        ctx = self.ctx(given="esphome", run=str(direct), plug_kind="sonoff-pow-elite")
        ctx.device = mock.Mock(sha_region=mock.Mock(return_value=self.ON_PLUG[direct]))
        with mock.patch.object(routes.source, "find_current_firmware", return_value="tasmota"), \
                mock.patch.object(update, "outdated", return_value=False), \
                mock.patch.object(routes, "sonoff_backup_exists", return_value=True):
            routes.settle_route(ctx)
        return ctx

    ON_PLUG = {True: hashlib.sha256(esphome.published_bootloader(chips.ESP32)).hexdigest(), False: "0" * 64}

    def names(self, state):
        return [step for step, _, _ in routes.steps_of(state)]

    def test_a_plug_with_esphomes_bootloader_goes_straight_to_esphome(self):
        ctx = self.settle(direct=True)
        self.assertEqual(self.names(ctx.state), ["A0", "A5", "L1", "M1", "I2"])

    def test_it_is_read_where_the_chip_keeps_it(self):
        ctx = self.settle(direct=True)
        ctx.device.sha_region.assert_called_once_with(chips.ESP32.bootloader_at,
                                                      len(esphome.published_bootloader(chips.ESP32)), timeout=30)

    def test_any_other_goes_through_the_hub_which_writes_it(self):
        steps = self.names(self.settle(direct=False).state)
        self.assertEqual(steps, ["A0", "A5", "L1", "X1", "X2", "D4", "D5", "K1", "I1", "I2"])

    def test_a_plug_that_will_not_say_goes_through_the_hub(self):
        ctx = self.ctx(plug_kind="sonoff-pow-elite")
        for failure in (DeviceError("no Berry"), OSError("gone")):
            ctx.device = mock.Mock(sha_region=mock.Mock(side_effect=failure))
            self.assertFalse(source.leaves_tasmota_direct(ctx))

    def test_a_layout_on_a_temporary_table_needs_the_hub_to_leave_it(self):
        # M1 would leave it there for good, whatever bootloader it has.
        ctx = self.ctx(plug_kind="sonoff-th-elite")
        ctx.device = mock.Mock(sha_region=mock.Mock(return_value=self.ON_PLUG[True]))
        self.assertFalse(source.leaves_tasmota_direct(ctx))

    def test_a_rerun_takes_the_way_it_began_and_does_not_ask_the_plug_again(self):
        for direct, next_step in ((True, "M1"), (False, "X1")):
            ctx = self.settle(direct=direct)
            ctx.device.sha_region.reset_mock()
            ctx.device.sha_region.return_value = self.ON_PLUG[not direct]  # by now the plug says otherwise
            self.assertEqual(routes.settle_route(ctx), ("esphome", "tasmota"))
            ctx.device.sha_region.assert_not_called()
            steps = self.names(ctx.state)
            self.assertEqual(steps[steps.index("L1") + 1], next_step)

    def test_a_run_begun_before_the_choice_keeps_to_the_way_it_went(self):
        ctx = self.ctx(source="tasmota", firmware="esphome")
        self.assertIn("D5", self.names(ctx.state))  # wrote nothing yet: as any new run
        ctx.state.mark("L1", DONE)     # that version's L1 wrote the bootloader
        self.assertEqual(self.names(ctx.state), ["A0", "A5", "L1", "M1", "I2"])

    def test_the_other_ways_from_tasmota_all_pass_through_the_hub(self):
        for running in ("safeboot", "committed", "untemplated"):
            self.assertIn("D5", [step for step, _, _ in espressif.steps_for("esphome", running)], running)
        self.assertIn("D5", [step for step, _, _ in espressif.steps_for("sonoff", "tasmota")])


if __name__ == "__main__":
    unittest.main()
