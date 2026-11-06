# Sonoff MINI Extreme

**Not ready yet: the tool does not convert this device.** its stock firmware starts in the emulator, run on another plug's flash as no dump of one is published, but goes to `factory mode`: it wants factory data only a real one holds. See [what is holding each one up](../README.md).

Sonoff's the **MINIR4**, an inline switch. Not the MINIR4M, the Matter one, which is locked.

| | |
|---|---|
| Chip | ESP32 |
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
