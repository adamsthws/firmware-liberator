"""Made-up eWeLink accounts for the emulators' stubs: the devices each lists
beside the emulated one, none of them emulated (AUTOFLASH_EMU_STUB_OTHERS,
stub_server.Stub.other_item). Every suite that reads out every device in an
account - both fault suites and the attended suite, in each chip family -
takes them from here, so each lists the same devices."""

from __future__ import annotations


def other_device(number, product, model=None, version="1.0.0"):
    """One of the account's other devices, made up (stub_server.Stub.other_item)."""
    return {"deviceid": f"10000000{number:02x}", "name": f"Test device {number}", "productModel": product,
            "model": model or f"SN-TEST-{product}-01", "uiid": 1, "fwVersion": version}


ACCOUNTS = {
    "mixed": [other_device(1, "TESTLIGHT"), other_device(2, "TESTSWITCH", version="2.1.0")],
    "alike": [other_device(1, "TESTLIGHT"), other_device(2, "TESTLIGHT"), other_device(3, "TESTLIGHT"),
              other_device(4, "TESTSWITCH")],
    "enough": [other_device(1, "TESTLIGHT"), other_device(2, "TESTSWITCH")],
    "many": [other_device(n, f"TESTMODEL{n}") for n in range(1, 8)],
}
