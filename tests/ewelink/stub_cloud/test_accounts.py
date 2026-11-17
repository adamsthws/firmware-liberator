"""The made-up accounts the emulators' stubs list beside the emulated device."""

import unittest

from tools.autoflash import readout
from tools.ewelink.stub_cloud import accounts, stub_server


class AccountTests(unittest.TestCase):
    def test_the_accounts_are_made_up_and_each_what_it_is_named_for(self):
        def alike(device):
            return device["productModel"], device["model"], device["fwVersion"]
        for name, devices in accounts.ACCOUNTS.items():
            with self.subTest(name):
                self.assertEqual(len({d["deviceid"] for d in devices}), len(devices))
                # None the tool converts: each only a first stage, as no other is emulated.
                self.assertFalse(any(readout.reads_out(stub_server.Stub.other_item(d)["itemData"]) for d in devices))
        self.assertLess(len({alike(d) for d in accounts.ACCOUNTS["alike"]}), len(accounts.ACCOUNTS["alike"]))
        self.assertEqual(len({alike(d) for d in accounts.ACCOUNTS["many"]}), len(accounts.ACCOUNTS["many"]))
        self.assertGreater(len(accounts.ACCOUNTS["many"]), 2)   # more than the API takes in a minute


if __name__ == "__main__":
    unittest.main()
