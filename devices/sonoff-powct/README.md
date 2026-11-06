# Sonoff POW Ring

Sonoff's the **POWCT**: a clamp-on energy meter with a small display. Its stock firmware calls itself `SN-ESP32D0-POWCT-01`.

**Tested in the emulator only.** The conversion has run from a public dump of a
stock one; no real one has been converted. The emulator has none of its hardware
to see, so the wiring below is what its sources say.

| | |
|---|---|
| Chip | ESP32 (4 MB flash) |
| Relay | GPIO21 |
| Status LED | GPIO13, inverted |
| Wi-Fi LED | GPIO15, inverted |
| Power meter | CSE7761: RX GPIO25, TX GPIO26, 38400 baud, even parity |
| Display | TM1621: CS GPIO17, data GPIO5, read GPIO23, write GPIO18 |
| Button | GPIO0, active low |

Pins from [ESPHome's page](https://devices.esphome.io/devices/sonoff-powct/); Blakadder has no template for it, so the Tasmota template in [`kinds.py`](../../tools/autoflash/kinds.py) was worked out from that table.

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **Flash layout**: Sonoff's, the same table as the S60's.
- **Calibration**: not carried over.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../docs/adding-a-device.md)
  says what was needed.
