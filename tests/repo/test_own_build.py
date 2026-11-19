"""tools/repo/own_build.py: a build of its own made from the published one, for
each family, and what the fault suites judge it by."""

import unittest

from tools.autoflash import imprint, kinds
from tools.autoflash.routes.beken import images as beken_images
from tools.autoflash.routes.espressif import images as espressif_images
from tools.autoflash.runs import ROOT
from tools.beken import rbl
from tools.repo import own_build


class TimeTests(unittest.TestCase):
    def test_built_an_hour_later_the_same_length(self):
        self.assertEqual(own_build.rebuilt("2026-11-10 15:55:44 +0000"), "2026-11-10 16:55:44 +0000")
        self.assertEqual(own_build.rebuilt("2026-10-21 23:35:20 +0100"), "2026-10-22 00:35:20 +0100")
        with self.assertRaises(own_build.OwnBuildError):
            own_build.rebuilt("Nov 10 2026 15:55:44")

    def test_the_time_is_moved_only_where_it_is_written_once(self):
        built = "2026-11-10 15:55:44 +0000"
        app, new = own_build.retimed(b"\0" + built.encode() + b"\0", built)
        self.assertEqual(app, b"\0" + new.encode() + b"\0")
        for app in (b"none here", (built + built).encode()):
            with self.subTest(app=app), self.assertRaises(own_build.OwnBuildError):
                own_build.retimed(app, built)


class FamilyTests(unittest.TestCase):
    """Each family's published build made a build of its own: whole, as its
    update service takes it, saying a build time no published build says."""

    def test_an_s60s_image_is_whole_and_says_another_build_time(self):
        kind = kinds.SONOFF_S60
        imprinted = espressif_images.image_for("ExampleNet", "example-password", kind=kind)
        built = espressif_images.image_pin(kind)["built"]
        app, new = own_build.espressif(imprinted, built)
        report = imprint.parse_esp_image(app)
        self.assertTrue(report["checksum_valid"])
        self.assertIsNot(report["sha256_valid"], False)
        self.assertEqual(len(app), len(imprinted))
        self.assertIn(new.encode(), app)
        self.assertNotIn(built.encode(), app)
        self.assertEqual(imprint.imprinted_ssid(app, espressif_images.published_image(kind)), "ExampleNet")

    def test_an_s61s_update_carries_an_rbl_of_the_app_saying_another_build_time(self):
        pin = beken_images.pin(kinds.SONOFF_S61, "esphome")
        published = (ROOT / pin["file"]).read_bytes()
        data, new = own_build.beken(published, pin["built"], "esphome-sonoff-s61")
        # The UF2's 256-byte pieces, each at its place in the block (uf2.write).
        pieces = b"".join(data[at + 32:at + 32 + 256] for at in range(512, len(data), 512))
        package = pieces[:rbl.HEADER + rbl.header(pieces).size_package]
        app = rbl.unpack(package)
        self.assertIn(new.encode(), app)
        self.assertNotIn(pin["built"].encode(), app)
        self.assertEqual(rbl.header(package).version, rbl.header(published).version)
        self.assertEqual(rbl.unpack(published).replace(pin["built"].encode(), new.encode()), app)


class ProblemTests(unittest.TestCase):
    PUBLISHED = "2026-11-10 15:55:44 +0000"
    OWN = "2026-11-10 16:55:44 +0000"
    GOOD = {"own": OWN, "refused": True, "refused_built": OWN, "built_now": PUBLISHED,
            "calibration": "stock store, 1.02", "calibration_now": "stock store, 1.02"}

    def test_left_be_then_replaced_with_its_calibration_kept_passes(self):
        self.assertIsNone(own_build.problem(self.GOOD, self.PUBLISHED))

    def test_each_thing_wrong_is_named(self):
        for change, verdict in (({"refused": False}, "FAIL-NOT-REFUSED"),
                                ({"refused_built": self.PUBLISHED}, "FAIL-REPLACED-UNASKED"),
                                ({"built_now": self.OWN}, "FAIL-NOT-REPLACED"),
                                ({"calibration_now": "defaults"}, "FAIL-CALIBRATION-LOST")):
            with self.subTest(change=change):
                self.assertEqual(own_build.problem({**self.GOOD, **change}, self.PUBLISHED), verdict)


if __name__ == "__main__":
    unittest.main()
