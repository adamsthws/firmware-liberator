"""A readout of a Beken device: the stock firmware found in the offered file,
the round trip driven through the proven helpers, nothing sent without PROCEED."""
import unittest
from types import SimpleNamespace
from unittest import mock

from tools.autoflash import readout_beken as rb
from tools.beken import fota_wrap, layouts, rbl


def a_ctx() -> SimpleNamespace:
    state = SimpleNamespace(_d={}, set_input=lambda k, v: state._d.__setitem__(k, v),
                            input=lambda k: state._d.get(k), require=lambda *k: [state._d[x] for x in k])
    state._d["plug_ip"] = "192.0.2.60"
    state._d["plug_mac"] = "12:34:56:ab:cd:ef"
    return SimpleNamespace(state=state, dir=None, listen_ip=lambda: "192.0.2.141",
                           email=lambda: "me@example.com", ewelink_env=lambda: {"EWELINK_PASSWORD": "pw"})


def s61_app() -> bytes:
    """A minimal app carrying the known 4 MB BK7238 table, as layouts reads it."""
    from tests.beken.test_layouts import an_app
    return an_app(layouts.BEKEN_4M.partitions)


class StockFromOfferedTests(unittest.TestCase):
    def offered(self, data):
        return [SimpleNamespace(name="user1.bin", version="1.0.2", data=data)]

    def test_the_offered_stock_fota_gives_its_rbl_and_table(self):
        app = s61_app()
        rbl_image = rbl.pack(app, name="app", version="1.0.2")
        fota = fota_wrap.wrap(rbl_image, name="FWSW-01-S61-BK7238", version="1.0.2")
        found = rb.stock_from_offered(self.offered(fota))
        self.assertIsNotNone(found)
        self.assertEqual(found.rbl, rbl_image)
        self.assertEqual(found.layout.key, layouts.BEKEN_4M.key)

    def test_a_file_that_is_not_a_known_table_is_passed_over(self):
        self.assertIsNone(rb.stock_from_offered(self.offered(b"not a fota")))


class ReadoutImageTests(unittest.TestCase):
    def test_it_refuses_a_file_that_is_not_the_pin(self):
        with mock.patch.object(rb, "PIN", {"file": "artifacts/openbeken-readout-1.18.320-1.rbl",
                                           "sha256": "0" * 64}):
            with self.assertRaisesRegex(rb.Abort, "not the file firmware.json pins"):
                rb.readout_image()

    def test_the_pinned_file_is_returned(self):
        self.assertGreater(len(rb.readout_image()), 1000)


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        for name in ("say", "tell", "transient_begin", "transient_end", "progress", "progress_clear"):
            patch = mock.patch.object(rb.output, name)
            patch.start()
            self.addCleanup(patch.stop)
        self.found = rb.Stock(b"RBL\0stock", "1.0.2", layouts.BEKEN_4M)
        self.device = {"deviceid": "1000abcdef"}
        self.ctx = a_ctx()

    def test_nothing_is_sent_without_proceed(self):
        with mock.patch.object(rb.prompts, "warning_box"), \
                mock.patch.object(rb.prompts, "require_word", return_value=False), \
                mock.patch.object(rb, "deliver_readout") as sent:
            with self.assertRaises(rb.NothingWritten):
                rb.read_out(self.ctx, "FWSW-01-S61-BK7238", "1.0.2", self.device, self.found)
        sent.assert_not_called()

    def test_proceed_runs_the_round_trip_in_order(self):
        order = []
        with mock.patch.object(rb.prompts, "warning_box"), \
                mock.patch.object(rb.prompts, "require_word", return_value=True), \
                mock.patch.object(rb, "deliver_readout", side_effect=lambda *a: order.append("deliver")), \
                mock.patch.object(rb, "wait_for_openbeken", side_effect=lambda ctx: order.append("wait") or "192.0.2.60"), \
                mock.patch.object(rb.openbeken, "info", return_value={"build": "obk", "chipset": "BK7238"}), \
                mock.patch.object(rb, "read_whole_flash", side_effect=lambda ctx, host: order.append("read") or b"\xff" * rb.WHOLE_FLASH), \
                mock.patch.object(rb.plugs, "save_beken_backup", return_value=(None, False)), \
                mock.patch.object(rb, "revert_to_sonoff", side_effect=lambda *a: order.append("revert")), \
                mock.patch.object(rb, "wait_for_sonoff", side_effect=lambda ctx, did: order.append("sonoff") or True):
            flash, info, back = rb.read_out(self.ctx, "FWSW-01-S61-BK7238", "1.0.2", self.device, self.found)
        self.assertEqual(order, ["deliver", "wait", "read", "revert", "sonoff"])
        self.assertEqual((len(flash), info["openbeken"], back), (rb.WHOLE_FLASH, "obk", True))

    def test_revert_uses_the_revert_command_and_the_offered_rbl(self):
        served = {}
        with mock.patch.object(rb.onward, "serve_to_plug",
                               side_effect=lambda ctx, image, what, fetch, unstick=None: served.update(image=image, what=what) or 1.0), \
                mock.patch.object(rb.openbeken, "command") as command:
            rb.revert_to_sonoff(self.ctx, "192.0.2.60", self.found)
            fetch = rb.onward.serve_to_plug.call_args.args[3]
            fetch("http://192.0.2.141:8088/firmware.rbl")
        self.assertEqual(served["image"], b"RBL\0stock")
        command.assert_called_once_with("192.0.2.60", "AutoflashRevertToStock http://192.0.2.141:8088/firmware.rbl")


if __name__ == "__main__":
    unittest.main()
