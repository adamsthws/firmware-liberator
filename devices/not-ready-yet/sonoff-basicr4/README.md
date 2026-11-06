# Sonoff BASICR4

**Not ready yet: the tool does not convert this device.** its stock firmware stops at start-up in the emulator (`espInit`: `ESP_ERR_NOT_FOUND`), run on another plug's flash, as no dump of one is published. It may need what only a real one's flash holds. See [what is holding each one up](../README.md).

Sonoff's the ESP32-C3 inline switch.

| | |
|---|---|
| Chip | ESP32-C3 |
| Relay | GPIO4 |
| LED | GPIO6 |
| Button | GPIO9, active low |

Pins from [Blakadder's template](https://templates.blakadder.com/sonoff_BASICR4.html), whose warning stands: **its circuit has no transformer, and its low-voltage side sits at mains potential. Never connect anything to its pins with it on the mains.**

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../../docs/adding-a-device.md)
  says what was needed.
