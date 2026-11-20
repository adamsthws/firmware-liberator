"""Every device's ESPHome config and what they share (devices/shared-esphome/): each
has the base, its family's Wi-Fi, each relay's settings, and each meter's
totals and overload, and none keeps a copy of its own of any of them."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "devices/shared-esphome"
METER = re.compile(r"platform: *(cse7766|cse7761|bl0942|bl0937|bl0940|hlw8012|hlw8032|ade7953)")
RELAY = re.compile(r'^  (relay\w*): !include \{file: \.\./shared-esphome/relay\.yaml, vars: \{relay: (\w+), '
                   r'name: "([^"]+)", default: "(true|false)", countdown: "([^"]+)"\}\}$', re.M)
ENERGY = re.compile(r'^  energy\w*: !include \{file: \.\./shared-esphome/energy-totals\.yaml, '
                    r'vars: \{energy: (\w+), id_end: "(\w*)", name_end: "([^"]*)"\}\}$', re.M)
OVERLOAD = re.compile(r'^  overload\w*: !include \{file: \.\./shared-esphome/overload\.yaml, vars: \{relay: (\w+), '
                      r'power: (\w+), id_end: "(\w*)", name_end: "([^"]*)", max: "(\d+)", default: "(\d+)"\}\}$', re.M)
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


if __name__ == "__main__":
    unittest.main()
