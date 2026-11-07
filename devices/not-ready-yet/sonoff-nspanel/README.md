# Sonoff NSPanel

**Not ready yet: the tool does not convert this device.** its stock firmware boots in the emulator, from a whole dump, but turns the installer down after reading the first 24 bytes of the file. Why is not known yet. See [what is holding each one up](../README.md).

Sonoff's the wall panel with a touch screen and two relays. Its stock firmware calls itself `E32-MSW-NX`.

The conversion runs in the emulator from a public dump of a stock one. The emulator
has none of its hardware to see, so the wiring below is what its sources say.

| | |
|---|---|
| Chip | ESP32 (4 MB flash) |
| Relays | GPIO22, GPIO19 |
| Screen power | GPIO4, on when low |
| Buttons | GPIO14, GPIO27, active low |
| Buzzer | GPIO21 |
| Thermistor | GPIO38 |

Pins from [Blakadder's template](https://templates.blakadder.com/sonoff_NSPanel.html).

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **Screen**: it keeps Sonoff's interface, which needs Sonoff's firmware behind it: after a conversion the panel's buttons and relays work, and its screen shows no useful page until you load one of your own. Blakadder's page links Tasmota's driver for it.
- **Flash layout**: as the S60's but for an unused entry, so its table is its own ([`artifacts/sonoff-nspanel-partition-table.bin`](../../../artifacts/README.md)).
- **Calibration**: not carried over.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../../docs/adding-a-device.md)
  says what was needed.
