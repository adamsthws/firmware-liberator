"""The partition tables: each built table is the reviewed one, and a sound one."""
import hashlib
import unittest

from tools.espressif import layouts
from tools.espressif.safeboot import safeboot_migration


class SeenTests(unittest.TestCase):
    """Each layout pins the table a real device holds: its sector's hash, as
    read from a public dump or a real device's readout, never one made again
    from a list of entries (layouts.Seen)."""

    def test_each_layouts_table_is_the_one_seen_on_a_real_device(self):
        for layout in layouts.ALL:
            self.assertIsNotNone(layout.seen, layout.key)
            self.assertEqual(layout.table_sha256, layout.seen.sha256, layout.key)
            self.assertEqual(hashlib.sha256(layout.table).hexdigest(), layout.seen.sha256, layout.key)

    def test_each_says_where_it_was_read(self):
        from tools.espressif.emulator import public_dump
        for layout in layouts.ALL:
            if layout.seen.source != layouts.READOUT:
                public_dump.donor(layout.seen.source)  # a public dump's key


class TemporaryTableTests(unittest.TestCase):
    LAYOUT = layouts.SONOFF_TH_ELITE

    def rows(self):
        return {row[0]: row for row in self.LAYOUT.temporary_partitions()}

    def test_it_is_the_reviewed_table(self):
        self.assertEqual(hashlib.sha256(self.LAYOUT.temporary_table()).hexdigest(), self.LAYOUT.temporary_sha256)
        self.assertEqual(self.LAYOUT.leaving_sha256, self.LAYOUT.temporary_sha256)

    def test_it_is_a_valid_table_the_size_of_a_flash_sector(self):
        table = self.LAYOUT.temporary_table()
        self.assertEqual(len(table), safeboot_migration.TABLE_SIZE)
        parsed = safeboot_migration.parse_partition_sector(table)
        self.assertEqual(parsed["stored_md5"], parsed["calculated_md5"])
        self.assertEqual(parsed["entry_count"], len(self.LAYOUT.partitions()))

    def test_its_partitions_do_not_overlap_and_the_mover_fits_its_ota_0(self):
        rows = sorted(self.LAYOUT.temporary_partitions(), key=lambda row: row[3])
        for row, following in zip(rows, rows[1:]):
            self.assertLessEqual(row[3] + row[4], following[3], (row, following))
        self.assertEqual(self.rows()["ota_0"][3], layouts.MOVER_AT)
        self.assertGreaterEqual(self.rows()["ota_0"][4], 0xC0000)
        self.assertLessEqual(rows[-1][3] + rows[-1][4], 0x400000)

    def test_ota_1_is_where_the_devices_own_table_has_it(self):
        # The hub's Tasmota runs from there as the one table gives way to the other.
        own = {row[0]: row for row in self.LAYOUT.partitions()}
        self.assertEqual(self.rows()["ota_1"], own["ota_1"])
        self.assertEqual(self.rows()["nvs"][3], own["nvs"][3])

    def test_a_layout_with_ota_0_at_the_movers_place_has_no_need_of_one(self):
        for layout in (layouts.SONOFF_DUAL_OTA, layouts.SONOFF_NSPANEL):
            self.assertFalse(layout.needs_temporary)
            self.assertEqual(layout.leaving_sha256, layout.table_sha256)

    def test_a_layout_with_ota_0_elsewhere_must_have_one_reviewed(self):
        for layout in layouts.ALL:
            if layout.needs_temporary:
                self.assertEqual(hashlib.sha256(layout.temporary_table()).hexdigest(), layout.temporary_sha256)


class BasicR4Tests(unittest.TestCase):
    """Its firmware reads its table at 0xC000, and its own partitions come
    before its settings: the commit moves them too, and the temporary table
    leaves them out."""
    LAYOUT = layouts.SONOFF_BASICR4

    def test_its_tables_are_the_reviewed_ones_and_sound(self):
        for table, sha256 in ((self.LAYOUT.table, self.LAYOUT.table_sha256),
                              (self.LAYOUT.interim_table(), self.LAYOUT.interim_sha256),
                              (self.LAYOUT.temporary_table(), self.LAYOUT.temporary_sha256)):
            self.assertEqual(hashlib.sha256(table).hexdigest(), sha256)
            safeboot_migration.parse_partition_sector(table)  # no overlaps, all inside the flash
        self.assertEqual(self.LAYOUT.sonoff_table_at, 0xC000)
        self.assertEqual(layouts.by_table(self.LAYOUT.table_sha256), self.LAYOUT)

    def test_the_commit_moves_all_in_front_of_ota_0_from_its_first_partition(self):
        self.assertEqual(self.LAYOUT.low, 0xD000)
        moved = {row[0]: row for row in self.LAYOUT.interim_partitions()}
        own = {row[0]: row for row in self.LAYOUT.partitions()}
        for label in ("esp_secure_cert", "nvs_matter", "nvs_matter_bkp", "nvs", "otadata", "phy_init"):
            self.assertEqual(moved[label][3] - own[label][3], self.LAYOUT.moved_low - self.LAYOUT.low, label)
        self.assertEqual(self.LAYOUT.moved_low + self.LAYOUT.low_size, self.LAYOUT.ota[1])

    def test_behind_its_slots_is_fctry_and_nothing_else_has_anything(self):
        self.assertEqual(self.LAYOUT.after_slots, (("fctry", 0x3F0000, 0x6000),))
        for layout in layouts.ALL:
            if layout is not self.LAYOUT:
                self.assertEqual(layout.after_slots, (), layout.key)

    def test_its_temporary_table_has_only_what_runs_on_it_and_settings_that_fit_its_own(self):
        rows = {row[0]: row for row in self.LAYOUT.temporary_partitions()}
        self.assertEqual(set(rows), {"nvs", "otadata", "phy_init", "ota_0", "ota_1", "fctry"})
        self.assertEqual(rows["nvs"][3:5], (layouts.TEMPORARY_NVS, self.LAYOUT.nvs_size))
        self.assertEqual(rows["ota_0"][3], layouts.MOVER_AT)
        self.assertEqual(rows["ota_1"], {row[0]: row for row in self.LAYOUT.partitions()}["ota_1"])
        # Copied home whole, they come from nowhere they go to.
        self.assertLessEqual(layouts.TEMPORARY_NVS + self.LAYOUT.nvs_size, self.LAYOUT.nvs)


if __name__ == "__main__":
    unittest.main()
