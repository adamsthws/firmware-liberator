"""A dump's settings pointed at the emulator's network, reset plug or not."""

from __future__ import annotations

import json
import unittest

from tools.espressif.emulator import rewrite_nvs


class FrameParams(unittest.TestCase):
    def test_a_field_a_paired_plug_has_is_changed(self):
        out = rewrite_nvs.set_json_string(b'{"ssid":"old","a":1}\0\0', "ssid", "new")
        self.assertEqual(json.loads(out.split(b"\0")[0]), {"ssid": "new", "a": 1})

    def test_a_field_a_reset_plug_has_not_is_added(self):
        out = rewrite_nvs.set_json_string(b'{"a":1}\0\0', "ssid", "new")
        out = rewrite_nvs.set_json_number(out, "dis_port", 443)
        self.assertEqual(json.loads(out.split(b"\0")[0]), {"a": 1, "ssid": "new", "dis_port": 443})

    def test_a_field_twice_is_refused(self):
        with self.assertRaises(SystemExit):
            rewrite_nvs.set_json_string(b'{"ssid":"a","ssid":"b"}', "ssid", "new")


if __name__ == "__main__":
    unittest.main()
