# Devices not ready yet

Devices being worked on. **The tool does not convert these**: it turns one down
as it does any device it does not know. Their configs and templates are kept here
so the work is not lost; none has a published image, or has been run on a real one.

| Device | Where it stops, in the emulator | What would move it on |
|---|---|---|
| [NSPanel](sonoff-nspanel/README.md) | Boots from a whole dump, then turns the installer down after its first 24 bytes | finding out what it checks there |
| [BASICR4](sonoff-basicr4/README.md) | `espInit` stops with `ESP_ERR_NOT_FOUND` | a whole dump, or a real one's layout |
| [MINIR4](sonoff-minir4/README.md) | Goes to `factory mode` | a whole dump: it wants factory data |
| [M5 1-gang](sonoff-m5-1c/README.md), [2-gang](sonoff-m5-2c/README.md), [3-gang](sonoff-m5-3c/README.md) | Goes quiet, and never starts its Wi-Fi | a whole dump of one that is not the locked Matter version |

To work on one in the emulator, set `AUTOFLASH_NOT_READY_YET=1`, and build its
ESPHome with `tools/repo/update_firmware.py esphome --plug KEY`. When it converts
both ways, move its folder up to `devices/`, set its status to `Testers wanted` in
[`devices/status.json`](../status.json), and publish its image: the config's path is
part of the image, so it is built again from the new place.
