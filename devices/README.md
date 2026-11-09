# Devices

Each device has a folder here, whatever its status, and one status, set in
[`status.json`](status.json) and shown by its badge
([`tools/repo/device_status.py`](../tools/repo/device_status.py) makes them):

| Status | What it means | What the tool does |
|---|---|---|
| **Supported** | A real one has been converted and reported | converts it |
| **Testers wanted** | Converts in the emulator, or is added from its official file and a published pin map; no real one reported yet | converts it after a typed warning, and asks for the run's log |
| **Readout wanted** | Not converted yet, but a readout of it through the bridge is proven in the emulator to leave it as it was | turns a conversion down; reads it out in full |
| **Not supported yet** | Neither | turns it down, as a device it does not know; its readout is only what Sonoff's own firmware and eWeLink say ([`docs/readouts.md`](../docs/readouts.md)) |

## Not converted yet

Their configs and templates are kept so the work is not lost; none has a
published image, or has been converted on a real one.

| Device | Status | Where it stops, in the emulator | What would move it on |
|---|---|---|---|
| [BASICR4](sonoff-basicr4/README.md) | Readout wanted | Starts, on a stand-in flash with its table at 0xC000, and is read out | Tasmota and ESPHome finding the table at 0xC000; a readout of a real one, to know its whole table |
| [MINIR4](sonoff-minir4/README.md) | Readout wanted | Starts and is read out, with the factory and pair records it keeps; a conversion not tried yet | trying one in the emulator |
| [NSPanel](sonoff-nspanel/README.md) | Not supported yet | Boots from a whole dump, then turns the installer down after its first 24 bytes | finding out what it checks there |
| [M5 1-gang](sonoff-m5-1c/README.md), [2-gang](sonoff-m5-2c/README.md), [3-gang](sonoff-m5-3c/README.md) | Not supported yet | Goes quiet, and never starts its Wi-Fi | a whole dump of one that is not the locked Matter version |

To work on one in the emulator, set `AUTOFLASH_NOT_READY_YET=1`, and build its
ESPHome with `tools/repo/update_firmware.py esphome --plug KEY`. A device becomes
**Readout wanted** once its readout runs in the emulator and leaves it on its
Sonoff firmware; **Testers wanted** once it converts both ways there, and its image
is published.
