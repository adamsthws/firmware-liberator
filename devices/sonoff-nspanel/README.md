# Sonoff NSPanel

## Status

[![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-nspanel.json)](README.md)

## Where it stops

Its stock firmware boots in the emulator, from a whole dump, but turns the installer down after reading the first 24 bytes of the file: its updater is hardcoded to expect a three-record manifest (the ESP32 app plus two HMI screen images), and the bridge sends one record, so the file is rejected at the header. A three-record file cannot be made screen-safe in software — the two screen records drive the display MCU into firmware-upload mode before their payload is even read, and the app is activated only after all three records succeed — so getting past the gate needs real screen images and a real unit, or a different entry point. See [what is holding each one up](../README.md) and the [full trace](research.md#the-nspanel-wants-three-records).

## The device

Sonoff's wall panel with a touch screen and two relays. Its screen keeps Sonoff's
interface, which needs Sonoff's firmware behind it: after a conversion the panel's
buttons and relays work, and its screen shows no useful page until you load one of
your own. Blakadder's page links Tasmota's driver for it.

| | |
|---|---|
| Chip | ESP32 |
| Firmware name | `FWPNL-E32NSP-NSPANEL-ESP32D0WDV3` |
| Flash | 4 MB |
| Stock model | `E32-MSW-NX` |
| Relays | GPIO22, GPIO19 |
| Screen power | GPIO4, on when low |
| Buttons | GPIO14, GPIO27, active low |
| Buzzer | GPIO21 |
| Thermistor | GPIO38 |

Pins from [Blakadder's template](https://templates.blakadder.com/sonoff_NSPanel.html).

## Files

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **Flash layout**: as the S60's but for an unused entry, so its table is its own ([`artifacts/sonoff-nspanel-partition-table.bin`](../../artifacts/README.md)).
- **Calibration**: none, as it has no meter.
- **Back to Sonoff**: the tool writes back the device's own backup, taken before
  anything of Sonoff's is written over; without it, Sonoff's official firmware for
  it, with the identity the device keeps or your eWeLink account holds
  ([how](../../docs/what-happens.md#changing-firmware-later)).
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../docs/adding-a-device.md)
  says what was needed.
- **Research**: [`research.md`](research.md), why its updater refuses the bridge's file.
