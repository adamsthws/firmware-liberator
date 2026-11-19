"""Every device's ESPHome config: each relay has its power-on state, from
devices/power-on-state.yaml, and keeps a switch made just before a power cut."""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHARED = ROOT / "devices/power-on-state.yaml"
INCLUDE = re.compile(r'^  (power_on\w*): !include \{file: \.\./power-on-state\.yaml, '
                     r'vars: \{relay: (\w+), name: "([^"]+)", default: "(true|false)"\}\}$', re.M)


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


class PowerOnStateTests(unittest.TestCase):
    def configs(self):
        configs = sorted(ROOT.glob("devices/*/esphome.yaml"))
        self.assertTrue(configs)
        return configs

    def test_every_relay_has_its_own_power_on_state(self):
        for path in self.configs():
            config = path.read_text(encoding="utf-8")
            included = {relay: name for _, relay, name, _ in INCLUDE.findall(config)}
            with self.subTest(config=path.parent.name):
                self.assertTrue(relays(config))
                self.assertEqual(included, relays(config))
                self.assertEqual(len(INCLUDE.findall(config)), len(set(INCLUDE.findall(config))))

    def test_no_relay_restores_itself_before_its_choice_is_known(self):
        # Restored at setup, a relay set to "Off" would be on until on_boot.
        for path in self.configs():
            config = path.read_text(encoding="utf-8")
            for item in switches(config):
                if re.search(r"id: (\w+)", item).group(1) in relays(config):
                    with self.subTest(config=path.parent.name, switch=item.split("\n", 1)[0]):
                        self.assertNotIn("restore_mode:", item)

    def test_a_device_first_started_is_as_it_was_before_the_power_on_state(self):
        # On, as before, where the stock firmware's relay comes on; off elsewhere.
        on = {path.parent.name for path in self.configs()
              if 'default: "true"' in path.read_text(encoding="utf-8")}
        self.assertEqual(on, {"sonoff-s60", "sonoff-s61", "sonoff-basic-1gs"})

    def test_the_relay_is_set_up_doing_nothing_and_written_as_it_switches(self):
        shared = SHARED.read_text(encoding="utf-8")
        item = switches(shared)[0]
        self.assertIn("id: !extend ${relay}", item)
        self.assertIn("    restore_mode: DISABLED\n", item)
        for trigger in ("on_turn_on", "on_turn_off"):
            self.assertIn("- lambda: global_preferences->sync();", actions(item, trigger), trigger)

    def test_the_choice_is_saved_and_written_once_the_select_has_saved_it(self):
        shared = SHARED.read_text(encoding="utf-8")
        select = shared[shared.index("\nselect:\n"):]
        self.assertIn("    restore_value: true\n", select)
        self.assertEqual(actions(select, "on_value"), "      # The select saves its choice once on_value has run, "
                         "so the write waits\n      # for the next loop.\n      - delay: 0s\n"
                         "      - lambda: global_preferences->sync();\n")

    def test_the_choice_is_applied_after_the_relay_and_select_are_set_up(self):
        shared = SHARED.read_text(encoding="utf-8")
        # setup_priority::HARDWARE is 800, a template switch's 798, the meter's
        # (DATA) 600.
        self.assertIn("    - priority: 700\n", shared)
        for option in ("Off", "On", "Last state"):
            self.assertIn(f'      - "{option}"\n', shared)
        self.assertIn('initial_option: "Last state"', shared)
        self.assertIn("get_initial_state().value_or(${default})", shared)


if __name__ == "__main__":
    unittest.main()
