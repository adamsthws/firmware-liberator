"""board.py, the emulated Beken plug's board and its BL0937 meter."""

import re
import unittest
from pathlib import Path
from types import SimpleNamespace

from tools.beken.emulator import board

ROOT = Path(__file__).resolve().parents[3]
S61 = board.WIRING["sonoff-s61"]
OUTPUT_HIGH, OUTPUT_LOW, INPUT_PULLUP = 0x02, 0x00, 0x3C   # GPIO config words as the SDK writes them
# The emulator's device clock: 10000 instructions to 2 ms, run 1000 at a time.
SECONDS_PER_INSN, CHUNK = 2e-3 / 10000, 1000


class Gpio:
    """The emulator's GpioModel, as far as the board drives it: each pin's
    level (high by its pull-up at first), and an edge latched in INTSTA when
    the pin's interrupt takes it (falling, or rising) - an edge while the last
    is still latched is lost."""
    def __init__(self):
        self.levels, self.int_status, self.rising = {}, 0, set()

    def drive(self, pin, level, config):
        before = self.levels.get(pin, 1)
        if (before, level) == ((0, 1) if pin in self.rising else (1, 0)):
            self.int_status |= 1 << pin
        self.levels[pin] = level


class Emulator:
    """What attach() wires itself into: the GPIO config words, the pacing
    between chunks and the idle skip's question; and a firmware that takes
    the GPIO interrupt after each chunk, but not while `masked` says its
    interrupts are off (by the chunk's number), counting each pin's as
    OpenBeken does - or, with `change`, as ESPHome does through LibreTiny's
    change interrupt: a pulse when it reads the pin high, the edge it waits
    for switched each time."""
    def __init__(self, relay_on=True, sel_high=True, masked=lambda chunk: False, change=False):
        self.config = {S61.relay: OUTPUT_HIGH if relay_on else OUTPUT_LOW,
                       S61.sel: OUTPUT_HIGH if sel_high else OUTPUT_LOW,
                       S61.cf: INPUT_PULLUP, S61.cf1: INPUT_PULLUP}
        self.state = SimpleNamespace(insn_count=0, gpio_hw=Gpio())
        self.paced, self.masked, self.change = 0, masked, change
        self.taken = {S61.cf: 0, S61.cf1: 0}

    def _gpio_config_word(self, pin):
        return self.config.get(pin, 0)

    def _pacing(self, state):
        self.paced += 1

    def _fast_tick_idle(self, state):
        return True

    def run(self, seconds):
        """The pulses counted on CF and CF1 over `seconds` of the device's time."""
        before = dict(self.taken)
        gpio = self.state.gpio_hw
        for _ in range(round(seconds / SECONDS_PER_INSN / CHUNK)):
            self.state.insn_count += CHUNK
            self._pacing(self.state)
            if self.masked(self.paced):
                continue
            for pin in self.taken:
                if gpio.int_status >> pin & 1:
                    gpio.int_status &= ~(1 << pin)
                    if not self.change:
                        self.taken[pin] += 1
                        continue
                    self.taken[pin] += gpio.levels[pin]
                    gpio.rising ^= {pin}
        return tuple(self.taken[pin] - before[pin] for pin in (S61.cf, S61.cf1))


def attached(**kwargs):
    emu = Emulator(**kwargs)
    board.attach(emu, "sonoff-s61", seconds_per_insn=SECONDS_PER_INSN)
    return emu


class WiringTests(unittest.TestCase):
    def test_the_s61s_pins_are_its_openbeken_tables(self):
        table = (ROOT / "devices/sonoff-s61/drv_autoflash_sonoff_s61.c").read_text(encoding="utf-8")
        pins = {role: int(pin) for pin, role in re.findall(r"\{(\d+), IOR_(\w+), \d+\}", table)}
        self.assertEqual((pins["Relay"], pins["BL0937_CF"], pins["BL0937_CF1"], pins["BL0937_SEL"]),
                         (S61.relay, S61.cf, S61.cf1, S61.sel))

    def test_and_its_esphome_configs(self):
        config = (ROOT / "devices/sonoff-s61/esphome.yaml").read_text(encoding="utf-8")
        self.assertIn(f"cf_pin: P{S61.cf}\n", config)
        self.assertIn(f"cf1_pin: P{S61.cf1}\n", config)
        self.assertIsNotNone(re.search(rf"sel_pin:\s+number: P{S61.sel}\n", config), "SEL")
        self.assertIsNotNone(re.search(rf"id: relay\n(?:.*\n){{0,3}}\s+pin: P{S61.relay}\n", config), "relay")

    def test_one_hertz_is_the_dumps_factory_calibration_as_openbeken_has_it(self):
        # devices/sonoff-s61/research.md: what the import gives OpenBeken.
        self.assertAlmostEqual(S61.watts_per_hz, 1.334562, places=5)
        self.assertAlmostEqual(S61.volts_per_hz, 0.128238, places=5)
        self.assertAlmostEqual(S61.amps_per_hz, 0.011095, places=5)

    def test_a_kind_with_no_board_here_gets_none(self):
        emu = Emulator()
        self.assertIsNone(board.attach(emu, "sonoff-s60"))
        self.assertEqual(emu._pacing.__name__, "_pacing")


class MeterTests(unittest.TestCase):
    """The meter as OpenBeken reads it (drv_bl0937.c): falling edges counted
    for a second, times its calibration."""

    def test_the_relay_on(self):
        cf, cf1 = attached(sel_high=True).run(2.0)
        self.assertAlmostEqual(cf / 2 * S61.watts_per_hz, 100.0, delta=1.5)
        self.assertAlmostEqual(cf1 / 2 * S61.volts_per_hz, 230.0, delta=0.5)
        _, cf1 = attached(sel_high=False).run(2.0)
        self.assertAlmostEqual(cf1 / 2 * S61.amps_per_hz, 100 / 230, delta=0.015)

    def test_the_relay_off_measures_no_load_but_the_supply(self):
        cf, cf1 = attached(relay_on=False, sel_high=True).run(2.0)
        self.assertEqual(cf, 0)
        self.assertAlmostEqual(cf1 / 2 * S61.volts_per_hz, 230.0, delta=0.5)
        self.assertEqual(attached(relay_on=False, sel_high=False).run(2.0), (0, 0))

    def test_the_relay_and_sel_are_followed_as_the_firmware_switches_them(self):
        emu = attached(sel_high=True)
        emu.run(1.0)
        emu.config[S61.relay] = OUTPUT_LOW
        emu.config[S61.sel] = OUTPUT_LOW
        self.assertEqual(emu.run(1.0)[0], 0)
        emu.config[S61.relay] = OUTPUT_HIGH
        _, cf1 = emu.run(1.0)
        self.assertAlmostEqual(cf1 * S61.amps_per_hz, 100 / 230, delta=0.015)

    def test_a_pin_the_firmware_does_not_drive_is_low(self):
        """An input, or an output with its driver off (bit 3), is not high."""
        self.assertFalse(board.driven_high(INPUT_PULLUP | 0x02))
        self.assertFalse(board.driven_high(0x0A))
        self.assertTrue(board.driven_high(OUTPUT_HIGH))
        cf, _ = attached(relay_on=False).run(1.0)
        self.assertEqual(cf, 0)

    def test_edges_wait_for_a_firmware_slow_to_take_them(self):
        """Interrupts off for 2 ms in every 10, longer than the voltage's
        1.8 kHz period: no edge is lost, only late."""
        _, cf1 = attached(sel_high=True, masked=lambda chunk: chunk % 50 < 10).run(2.0)
        self.assertAlmostEqual(cf1 / 2 * S61.volts_per_hz, 230.0, delta=0.5)

    def test_but_a_firmware_that_never_takes_them_is_owed_a_quarter_second_at_most(self):
        emu = attached(sel_high=False, masked=lambda chunk: True)
        emu.run(1.0)
        emu.masked = lambda chunk: False
        _, cf1 = emu.run(1.0)
        self.assertLessEqual(cf1, round(100 / 230 / S61.amps_per_hz * 1.25) + 1)

    def test_what_cf1_owed_of_one_quantity_is_not_given_as_the_other(self):
        emu = attached(sel_high=True, masked=lambda chunk: True)
        emu.run(0.1)
        emu.config[S61.sel] = OUTPUT_LOW
        emu.masked = lambda chunk: False
        _, cf1 = emu.run(1.0)
        self.assertAlmostEqual(cf1 * S61.amps_per_hz, 100 / 230, delta=0.03)   # the edge latched as SEL went

    def test_esphomes_change_interrupt_counts_one_a_pulse(self):
        """With interrupts off 4 ms in every 10, a square wave's two edges a
        cycle would come to 1.5 kHz at most; a pulse is one interrupt,
        whichever edge it waits for."""
        cf, cf1 = attached(sel_high=True, change=True, masked=lambda chunk: chunk % 50 < 20).run(2.0)
        self.assertAlmostEqual(cf / 2 * S61.watts_per_hz, 100.0, delta=1.5)
        self.assertAlmostEqual(cf1 / 2 * S61.volts_per_hz, 230.0, delta=0.5)

    def test_the_emulators_own_pacing_still_runs_after_the_board(self):
        emu = attached()
        emu.run(0.01)
        self.assertEqual(emu.paced, 50)

    def test_the_idle_loop_is_skipped_only_while_no_edge_is_due(self):
        self.assertFalse(attached(sel_high=True)._fast_tick_idle(None))
        self.assertFalse(attached(relay_on=True, sel_high=False)._fast_tick_idle(None))
        self.assertTrue(attached(relay_on=False, sel_high=False)._fast_tick_idle(None))


class EsphomeTests(unittest.TestCase):
    """The meter as ESPHome's hlw8012 reads it on the S61's config: it drives
    SEL with its current mode, so a BL0937's SEL is inverted in the config."""

    def test_esphome_measures_the_voltage_with_sel_high(self):
        config = (ROOT / "devices/sonoff-s61/esphome.yaml").read_text(encoding="utf-8")
        inverted = re.search(rf"sel_pin:\s+number: P{S61.sel}\n\s+inverted: true\n", config) is not None
        current_mode = False   # ESPHome's first mode: voltage
        emu = attached(sel_high=current_mode != inverted)
        _, cf1 = emu.run(2.0)
        volts = cf1 / 2 * 1.218 * 2040 / 15397      # ESPHome's BL0937 voltage multiplier
        volts *= 1.1 / 1.218 * 879893 / 1e6         # and the config's calibration filter
        self.assertAlmostEqual(volts, 230.0, delta=0.5)


if __name__ == "__main__":
    unittest.main()
