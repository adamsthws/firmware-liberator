# Sonoff POW Elite 16A

Sonoff's DIN-rail power meter and switch with a small display, the **POWR316D**.
Its stock firmware calls itself `SN-ESP32D0-POWR3-01`, and so does the 20 A
POWR320D's, whose relay is a latching one on other pins: the tool converts a
plug eWeLink calls a POWR316D, and no other.

The conversion runs in the emulator from a public dump of a stock POWR316D. The
emulator has no relay, meter or display, so the wiring below is what its sources
say, not what was seen.

| | |
|---|---|
| Chip | ESP32 (4 MB flash) |
| Relay | GPIO13 |
| Status LED | GPIO18, inverted |
| Wi-Fi LED | GPIO5, inverted |
| Power meter | CSE7766 on UART, RX GPIO16, 4800 baud, even parity |
| Display | TM1621: CS GPIO25, data GPIO14, read GPIO26, write GPIO27 |
| Button | GPIO0, active low |

Pins from [ESPHome's page](https://devices.esphome.io/devices/sonoff-pow-elite-16a/)
and [Blakadder's template](https://templates.blakadder.com/sonoff_POWR316D.html),
which agree.

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what
  it reports.
- **Flash layout**: Sonoff's, the same table as the S60's
  ([`artifacts/sonoff-s60-partition-table.bin`](../../artifacts/README.md)).
- **Calibration**: not carried over. Its factory values are in the stock settings
  as the S60's are, but how its firmware applies them has not been read, so a
  converted one measures as Tasmota or ESPHome does uncalibrated.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../docs/adding-a-device.md)
  says what was needed.
