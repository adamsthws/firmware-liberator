"""Every device's ESPHome config and what they share (devices/shared-esphome/): each
has the base, its family's Wi-Fi, each relay's settings, and each meter's
totals, overload and derived readings, and none keeps a copy of its own of any
of them."""

import re
import unittest
from pathlib import Path

from tools.autoflash.esphome import COVER

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "devices/shared-esphome"
METER = re.compile(r"platform: *(cse7766|cse7761|bl0942|bl0937|bl0940|hlw8012|hlw8032|ade7953)")
RELAY = re.compile(r'^  (relay\w*): !include \{file: \.\./shared-esphome/relay\.yaml, vars: \{relay: (\w+), '
                   r'name: "([^"]+)", default: "(true|false)", countdown: "([^"]+)"\}\}$', re.M)
ENERGY = re.compile(r'^  energy\w*: !include \{file: \.\./shared-esphome/energy-totals\.yaml, '
                    r'vars: \{energy: (\w+), id_end: "(\w*)", name_end: "([^"]*)"\}\}$', re.M)
OVERLOAD = re.compile(r'^  overload\w*: !include \{file: \.\./shared-esphome/overload\.yaml, vars: \{relay: (\w+), '
                      r'power: (\w+), id_end: "(\w*)", name_end: "([^"]*)", max: "(\d+)", default: "(\d+)"\}\}$', re.M)
DERIVED = re.compile(r'^  derived\w*: !include \{file: \.\./shared-esphome/derived-power\.yaml, vars: \{voltage: (\w+), '
                     r'current: (\w+), power: (\w+), id_end: "(\w*)", name_end: "([^"]*)"\}\}$', re.M)
# A meter that does not measure its relay's own load, so has no overload.
NO_OVERLOAD = {"sonoff-powct": "its clamp measures a circuit of its own"}
# What a shared file holds, which no device's own config may hold again: the
# first of each is in its file as written here.
SHARED_ONLY = {
    "base.yaml": (r"^wifi:", r"^ota:", r"^web_server:", r"^captive_portal:", r"^preferences:",
                  r"platform: wifi_signal", r"reboot_timeout", r"action: autoflash_wifi", r"88491487UL",
                  r"platform: sntp", r"set_ap\("),
    "wifi-esp32.yaml": (r"AUTOFLASH-IMPRINT-",),
    "wifi-bk72xx.yaml": (r'ef40_read\("wifi-', r"struct Ef40"),
    "relay.yaml": (r"power-on state", r"restore_mode: RESTORE_", r"name: \"?Countdown"),
    "energy-totals.yaml": (r'name: "(Daily|Monthly|Yearly|Total) energy', r"energy_\w+_wh\) \+="),
    "overload.yaml": (r'name: "Overload', r"overload_threshold"),
    "derived-power.yaml": (r'name: "Apparent power', r'name: "(Reactive power|Power factor)', r"std::sqrt\(s \* s"),
}


def switches(config: str) -> list[str]:
    """Each item under the top-level switch:, as its text."""
    section = re.search(r"^switch:\n(.*?)(?=^\S|\Z)", config, re.M | re.S)
    return re.split(r"^  - ", section.group(1), flags=re.M)[1:] if section else []


def relays(config: str) -> dict[str, str]:
    """Each switch a person switches, the relays: its id and name."""
    found = {}
    for item in switches(config):
        if "internal: true" in item:
            continue
        ident, name = re.search(r"id: (\w+)", item), re.search(r'^    name: "(.+)"', item, re.M)
        if ident and name and name.group(1).startswith("Relay"):
            found[ident.group(1)] = name.group(1)
    return found


def actions(text: str, trigger: str) -> str:
    """What `trigger` (on_turn_on, on_value...) does, as its text."""
    found = re.search(rf"^    {trigger}:\n((?:      .*\n)*)", text, re.M)
    return found.group(1) if found else ""


def configs() -> dict[str, str]:
    return {path.parent.name: path.read_text(encoding="utf-8") for path in sorted(ROOT.glob("devices/*/esphome.yaml"))}


class SharedTests(unittest.TestCase):
    def test_every_device_has_the_base_and_its_familys_wifi(self):
        self.assertTrue(configs())
        for device, config in configs().items():
            family = "bk72xx" if re.search(r"(?m)^bk72xx:", config) else "esp32"
            with self.subTest(device=device):
                self.assertRegex(config, r"(?m)^  base: !include \.\./shared-esphome/base\.yaml$")
                self.assertRegex(config, rf"(?m)^  wifi: !include \.\./shared-esphome/wifi-{family}\.yaml$")

    def test_no_device_keeps_a_copy_of_what_is_shared(self):
        for device, config in configs().items():
            for shared, patterns in SHARED_ONLY.items():
                for pattern in patterns:
                    with self.subTest(device=device, shared=shared, pattern=pattern):
                        self.assertNotRegex(config, re.compile(pattern, re.M))

    def test_what_is_shared_is_in_its_shared_file(self):
        for shared, patterns in SHARED_ONLY.items():
            with self.subTest(shared=shared):
                self.assertRegex((SHARED / shared).read_text(encoding="utf-8"), re.compile(patterns[0], re.M))


class RelayTests(unittest.TestCase):
    def test_every_relay_has_its_own_settings(self):
        for device, config in configs().items():
            included = {relay: name for _, relay, name, _, _ in RELAY.findall(config)}
            with self.subTest(device=device):
                self.assertTrue(relays(config))
                self.assertEqual(included, relays(config))

    def test_each_relays_countdown_has_a_name_of_its_own(self):
        # Countdown, as the S60's and S61's first had it, then Countdown 2...
        for device, config in configs().items():
            names = [countdown for *_, countdown in RELAY.findall(config)]
            with self.subTest(device=device):
                self.assertEqual(names, ["Countdown"] + [f"Countdown {n}" for n in range(2, len(names) + 1)])

    def test_no_relay_restores_itself_before_its_choice_is_known(self):
        # Restored at setup, a relay set to "Off" would be on until on_boot.
        for device, config in configs().items():
            for item in switches(config):
                if re.search(r"id: (\w+)", item).group(1) in relays(config):
                    with self.subTest(device=device, switch=item.split("\n", 1)[0]):
                        self.assertNotIn("restore_mode:", item)

    def test_a_device_first_started_is_as_it_was_before_the_power_on_state(self):
        # On, as before, where the stock firmware's relay comes on; off elsewhere.
        on = {device for device, config in configs().items() if 'default: "true"' in config}
        self.assertEqual(on, {"sonoff-s60", "sonoff-s61", "sonoff-basic-1gs"})

    def test_the_relay_is_set_up_doing_nothing_and_written_as_it_switches(self):
        shared = (SHARED / "relay.yaml").read_text(encoding="utf-8")
        item = switches(shared)[0]
        self.assertIn("id: !extend ${relay}", item)
        self.assertIn("    restore_mode: DISABLED\n", item)
        for trigger in ("on_turn_on", "on_turn_off"):
            self.assertIn("- lambda: global_preferences->sync();", actions(item, trigger), trigger)

    def test_the_choice_is_saved_and_written_once_the_select_has_saved_it(self):
        shared = (SHARED / "relay.yaml").read_text(encoding="utf-8")
        select = shared[shared.index("\nselect:\n"):shared.index("\nnumber:\n")]
        self.assertIn("    restore_value: true\n", select)
        self.assertEqual(actions(select, "on_value"), "      # The select saves its choice once on_value has run, "
                         "so the write waits\n      # for the next loop.\n      - delay: 0s\n"
                         "      - lambda: global_preferences->sync();\n")

    def test_the_choice_is_applied_after_the_relay_and_select_are_set_up(self):
        shared = (SHARED / "relay.yaml").read_text(encoding="utf-8")
        # setup_priority::HARDWARE is 800, a template switch's 798, the meter's
        # (DATA) 600.
        self.assertIn("    - priority: 700\n", shared)
        for option in ("Off", "On", "Last state"):
            self.assertIn(f'      - "{option}"\n', shared)
        self.assertIn('initial_option: "Last state"', shared)
        self.assertIn("get_initial_state().value_or(${default})", shared)


class MeterTests(unittest.TestCase):
    def meters(self):
        found = {device: config for device, config in configs().items() if METER.search(config)}
        self.assertEqual(set(found), {"sonoff-s60", "sonoff-s61", "sonoff-pow-elite", "sonoff-powct", "sonoff-dualr3"})
        return found

    def test_every_meter_channel_has_its_energy_totals_and_its_overload(self):
        for device, config in self.meters().items():
            energy = [key for _, key, _ in ENERGY.findall(config)]
            overload = [key for _, _, key, *_ in OVERLOAD.findall(config)]
            with self.subTest(device=device):
                self.assertTrue(energy)
                self.assertEqual(overload, [] if device in NO_OVERLOAD else energy)

    def test_a_channels_ids_and_names_end_alike(self):
        # "" and "" for one channel; "_1" and " 1" for the first of two.
        for device, config in self.meters().items():
            for energy, key, suffix in ENERGY.findall(config):
                with self.subTest(device=device, energy=energy):
                    self.assertEqual(key.replace("_", " "), suffix)
                    self.assertTrue(energy.endswith(key))
                    self.assertRegex(config, rf"(?m)^\s+id: {energy}$")

    def test_each_overload_switches_off_its_own_relay_on_its_own_power(self):
        for device, config in self.meters().items():
            for relay, power, key, _, most, default in OVERLOAD.findall(config):
                with self.subTest(device=device, relay=relay):
                    self.assertIn(relay, relays(config))
                    self.assertRegex(config, rf"(?m)^\s+id: {power}$")
                    self.assertTrue(power.endswith(key))
                    self.assertLessEqual(int(default), int(most))

    def test_every_meter_channel_has_its_derived_readings(self):
        # Of every channel with energy totals, the POWCT's clamp too: its
        # voltage, current and power are of the one circuit.
        for device, config in self.meters().items():
            derived = [(key, suffix) for *_, key, suffix in DERIVED.findall(config)]
            with self.subTest(device=device):
                self.assertEqual(derived, [(key, suffix) for _, key, suffix in ENERGY.findall(config)])
                for voltage, current, power, key, _ in DERIVED.findall(config):
                    for source in (voltage, current, power):
                        self.assertRegex(config, rf"(?m)^\s+id: {source}$")
                    self.assertTrue(current.endswith(key) and power.endswith(key))

    def test_a_meter_averaged_on_timers_of_its_own_hands_each_reading_over(self):
        # A CSE7766's voltage, current and power each average over a second
        # from a moment of their own: worked out from the averages, a load
        # changing within the second reads as reactive.
        for device, config in self.meters().items():
            if "platform: cse7766" in config:
                with self.subTest(device=device):
                    meter = re.search(r"(?ms)^  - platform: cse7766\n(.*?)(?=^  - |^\S|\Z)", config).group(1)
                    for reading, hands in (("voltage", "id(derived_frame)[0] ="), ("power", "id(derived_frame)[1] ="),
                                           ("current", "id(derived_reading).execute(")):
                        part = re.search(rf"(?ms)^    {reading}:\n(.*?)(?=^    \w|\Z)", meter).group(1)
                        self.assertIn(hands, part, reading)
                        self.assertLess(part.index(hands), part.index("throttle_average"), reading)

    def test_the_derived_readings_are_not_the_meters_own(self):
        # A CSE7766's own come from its raw readings, and log each impossible
        # one: many a second under a resistive load.
        for device, config in self.meters().items():
            with self.subTest(device=device):
                self.assertNotRegex(config, r"(?m)^    (apparent_power|reactive_power|power_factor)\w*:")

    def test_a_resistive_load_has_no_reactive_power_within_the_meters_accuracy(self):
        # sqrt(S^2 - P^2) turns the meter's 1% between P and V*I on a heater
        # into up to 15% of S: it read 0-148 var on a 1 kW radiator.
        shared = (SHARED / "derived-power.yaml").read_text(encoding="utf-8")
        self.assertIn("publish_state(s - a > 0.015f * s ? std::sqrt(s * s - a * a) : 0.0f);", shared)

    def test_a_load_switching_gives_no_reactive_power(self):
        # A 2 kW radiator's thermostat switching read up to 1782 var and a power
        # factor of 0.66 for a second or three: the meter's power follows its
        # current behind. Only a second like the last one gives them.
        shared = (SHARED / "derived-power.yaml").read_text(encoding="utf-8")
        derived = shared[shared.index("id(apparent_power${id_end}).publish_state(s);"):]
        self.assertLess(derived.index("if (!steady) return;"), derived.index("id(reactive_power${id_end})"))
        self.assertLess(derived.index("if (!steady) return;"), derived.index("id(power_factor${id_end})"))

    def test_the_s60s_derived_readings_keep_their_names(self):
        # Home Assistant keeps their history by name.
        shared = (SHARED / "derived-power.yaml").read_text(encoding="utf-8")
        for name in ("Apparent power", "Reactive power", "Power factor"):
            self.assertIn(f'    name: "{name}${{name_end}}"\n', shared)
        self.assertIn('vars: {voltage: voltage, current: current, power: power, id_end: "", name_end: ""}',
                      configs()["sonoff-s60"])

    def test_a_total_is_added_to_by_each_rise_of_its_energy_and_starts_again_by_the_date(self):
        shared = (SHARED / "energy-totals.yaml").read_text(encoding="utf-8")
        self.assertIn("    source_id: ${energy}\n", shared)
        self.assertIn("double wh = x >= last ? x - last : x;", shared)
        self.assertIn("if (counted == today) return;", shared)

    def test_the_s60s_totals_keep_the_ids_they_were_restored_by(self):
        # Their restored values are found by id: renamed, the totals start again.
        shared = (SHARED / "energy-totals.yaml").read_text(encoding="utf-8")
        for period in ("daily", "monthly", "yearly", "total"):
            self.assertIn(f"  - id: energy_{period}_wh${{id_end}}\n", shared)
        self.assertIn('vars: {energy: energy, id_end: "", name_end: ""}', configs()["sonoff-s60"])


def section(config: str, name: str) -> str:
    """A top-level section of a config (uart:, sensor: ...), as its text."""
    found = re.search(rf"^{name}:\n(.*?)(?=^\S|\Z)", config, re.M | re.S)
    return found.group(1) if found else ""


class MotorBuildTests(unittest.TestCase):
    """The Dual R3's build for a motor (esphome-motor.yaml): its own config, so
    that a config people copy stays one file, held here to esphome.yaml's
    board, with what is shared included as there - but no relay's settings,
    as neither output may come on at a start or on its own."""

    def setUp(self):
        self.relays = (ROOT / "devices/sonoff-dualr3/esphome.yaml").read_text(encoding="utf-8")
        self.motor = (ROOT / "devices/sonoff-dualr3/esphome-motor.yaml").read_text(encoding="utf-8")

    def test_its_board_is_the_relay_builds(self):
        for name in ("esp32", "logger", "uart", "status_led", "sensor"):
            with self.subTest(name):
                self.assertTrue(section(self.relays, name))
                self.assertEqual(section(self.motor, name).strip(), section(self.relays, name).strip())
        pins = re.compile(r"^      number: (GPIO\d+)$|^    pin: (GPIO\d+)$", re.M)
        for name in ("switch", "binary_sensor"):
            with self.subTest(name):
                self.assertEqual(pins.findall(section(self.motor, name)), pins.findall(section(self.relays, name)))
        # The pins Tasmota held through its restart, released alike.
        self.assertIn(self.relays[self.relays.index("gpio_hold_dis"):self.relays.index("# Adopting")].strip(),
                      self.motor)

    def test_it_is_the_same_device_and_release(self):
        for field in (r"^  comment: (.+)$", r"^    name: (liberator\.\S+)$", r'^    version: "(.+)"$'):
            self.assertEqual(re.search(field, self.motor, re.M).group(1), re.search(field, self.relays, re.M).group(1))
        self.assertIn("devices/sonoff-dualr3/esphome-motor.yaml@", self.motor)

    def test_what_is_shared_is_included_as_in_the_relay_build_but_a_relays_settings(self):
        for pattern in (ENERGY, DERIVED):
            self.assertEqual(pattern.findall(self.motor), pattern.findall(self.relays))
        self.assertRegex(self.motor, r"(?m)^  base: !include \.\./shared-esphome/base\.yaml$")
        self.assertRegex(self.motor, r"(?m)^  wifi: !include \.\./shared-esphome/wifi-esp32\.yaml$")
        self.assertEqual(RELAY.findall(self.motor), [])
        self.assertEqual(OVERLOAD.findall(self.motor), [])
        for shared, patterns in SHARED_ONLY.items():
            for pattern in patterns:
                with self.subTest(shared=shared, pattern=pattern):
                    self.assertNotRegex(self.motor, re.compile(pattern, re.M))

    def test_neither_output_is_switched_on_its_own_nor_on_with_the_other_nor_at_a_start(self):
        items = {re.search(r"id: (\w+)", item).group(1): item for item in switches(self.motor)}
        self.assertEqual(set(items), {"relay", "relay_2"})
        self.assertEqual(relays(self.motor), {})
        for ident, other in (("relay", "relay_2"), ("relay_2", "relay")):
            with self.subTest(ident):
                self.assertIn("    internal: true\n", items[ident])
                self.assertIn("    restore_mode: ALWAYS_OFF\n", items[ident])
                self.assertIn(f"    interlock: [{other}]\n", items[ident])
                self.assertIn("    interlock_wait_time: ", items[ident])

    def test_its_cover_drives_one_output_each_way_and_stops_both(self):
        cover = section(self.motor, "cover")
        self.assertIn("    open_action:\n      - switch.turn_on: relay\n", cover)
        self.assertIn("    close_action:\n      - switch.turn_on: relay_2\n", cover)
        self.assertIn("    stop_action:\n      - switch.turn_off: relay\n      - switch.turn_off: relay_2\n", cover)
        self.assertIn(f'    name: "{COVER}"\n', cover)
        for name in ("Open duration", "Close duration"):
            self.assertIn(f'    name: "{name}"\n', section(self.motor, "number"))


if __name__ == "__main__":
    unittest.main()
