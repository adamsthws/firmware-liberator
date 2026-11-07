# Sonoff MINI Extreme

## Status

[![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-minir4.json)](README.md)

## Where it stops

Its stock firmware starts in the emulator, run on another plug's flash as no dump of one is published, but goes to `factory mode`: it wants factory data only a real one holds. See [what is holding each one up](../README.md).

## The device

Sonoff's the **MINIR4**, an inline switch. Not the MINIR4M, the Matter one, which is locked.

| | |
|---|---|
| Chip | ESP32 |
| Firmware name | `FWSW-E32R4-MINIR4-ESP32D0WDR2V3` |
| Relay | GPIO26 |
| LED | GPIO19, inverted |
| Button | GPIO0, active low |
| Wall switch | GPIO27 |

Pins from [Blakadder's template](https://templates.blakadder.com/sonoff_MINIR4.html).

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../../docs/adding-a-device.md)
  says what was needed.
