# Sonoff POW Elite 16A

## Status

[![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-pow-elite.json)](README.md)

## Testing

**In the emulator:** it converts, from a public dump of a stock one, to Tasmota and to ESPHome and back to Sonoff's firmware. The emulator has none of its hardware but its CSE7766 meter, which Tasmota and ESPHome both read, so the rest of its wiring below is what its sources say, not what was seen.

**Worth checking:** whether the relay, display, button and LEDs work, and what the meter reads against a load you know.

## The device

Sonoff's DIN-rail power meter and switch with a small display, the **POWR316D**.
Its stock model, below, is also the 20 A POWR320D's, whose relay is a latching one
on other pins: the tool converts a plug eWeLink calls a POWR316D, and no other.

| | |
|---|---|
| Chip | ESP32 |
| Firmware name | `FWSW-E32POWP-POWPRO-ESP32D0WDV3` |
| Flash | 4 MB |
| Stock model | `SN-ESP32D0-POWR3-01` |
| Relay | GPIO13 |
| Status LED | GPIO18, inverted |
| Wi-Fi LED | GPIO5, inverted |
| Power meter | CSE7766 on UART, RX GPIO16, 4800 baud, even parity |
| Display | TM1621: CS GPIO25, data GPIO14, read GPIO26, write GPIO27 |
| Button | GPIO0, active low |

Pins from [ESPHome's page](https://devices.esphome.io/devices/sonoff-pow-elite-16a/)
and [Blakadder's template](https://templates.blakadder.com/sonoff_POWR316D.html),
which agree.

## Files

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what
  it reports.
- **Flash layout**: Sonoff's, the same table as the S60's
  ([`artifacts/sonoff-s60-partition-table.bin`](../../artifacts/README.md)).
- **Calibration**: not carried over. Its factory values are in the stock settings
  as the S60's are, but how its firmware applies them has not been read, so a
  converted one measures as Tasmota or ESPHome does uncalibrated.
- **Back to Sonoff**: the tool writes back the device's own backup, taken before
  anything of Sonoff's is written over; without it, Sonoff's official firmware for
  it, with the identity the device keeps or your eWeLink account holds
  ([how](../../docs/what-happens.md#changing-firmware-later)).
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../docs/adding-a-device.md)
  says what was needed.
