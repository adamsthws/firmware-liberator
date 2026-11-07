# Sonoff POW Ring

## Status

[![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-powct.json)](README.md)

## Testing

**In the emulator:** it converts, from a public dump of a stock one, to Tasmota and to ESPHome and back to Sonoff's firmware. The emulator has none of its hardware, so its wiring below is what its sources say, not what was seen.

**Worth checking:** whether the relay, display, button and LEDs work, and what the meter reads against a load you know.

## The device

Sonoff's the **POWCT**: a clamp-on energy meter with a small display. Its stock firmware calls itself `SN-ESP32D0-POWCT-01`.

| | |
|---|---|
| Chip | ESP32 (4 MB flash) |
| Firmware name | `FWSW-E32POWCT-POWCT-ESP32D0WDV3` |
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
