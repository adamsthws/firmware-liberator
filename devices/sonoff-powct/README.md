# Sonoff POW Ring

## Status

[![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-powct.json)](README.md)

## Testing

**In the emulator:** it converts, from a public dump of a stock one, to Tasmota and to ESPHome and back to Sonoff's firmware. The emulator has none of its hardware but its CSE7761 meter, which ESPHome reads, so the rest of its wiring below is what its sources say, not what was seen.

**Worth checking:** whether the relay, display, button and LEDs work, and what the meter reads against a load you know.

## The device

Sonoff's clamp-on energy meter with a small display, the **POWCT**.

| | |
|---|---|
| Chip | ESP32 |
| Firmware name | `FWSW-E32POWCT-POWCT-ESP32D0WDV3` |
| Flash | 4 MB |
| Stock model | `SN-ESP32D0-POWCT-01` |
| Relay | GPIO21 |
| Status LED | GPIO13, inverted |
| Wi-Fi LED | GPIO15, inverted |
| Power meter | CSE7761: RX GPIO25, TX GPIO26, 38400 baud, even parity |
| Display | TM1621: CS GPIO17, data GPIO5, read GPIO23, write GPIO18 |
| Button | GPIO0, active low |

Pins from [ESPHome's page](https://devices.esphome.io/devices/sonoff-powct/). Blakadder has no template for it; the Tasmota template in [`kinds.py`](../../tools/autoflash/kinds.py) is the one Tasmota's own TM1621 driver gives (`xdrv_87_esp32_sonoff_tm1621.ino`). Its meter's RX is CSE7761 Rx 2, which tells Tasmota the meter is a POWCT's, with a current clamp, and not a Dual R3's.

On ESPHome 2026.10, the meter reads the clamp as a Dual R3's shunt, 3.2 times too high, and the config divides by π as ESPHome's page says, leaving it about 2% high. ESPHome 2026.11.0 reads it as a clamp (`current_input: current_transformer`), and the build with it sets that and drops the division: [`test_firmware_pins.py`](../../tests/repo/test_firmware_pins.py) fails until it does.

## Files

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **Flash layout**: Sonoff's, the same table as the S60's.
- **Calibration**: not carried over.
- **Back to Sonoff**: the tool writes back the device's own backup, taken before
  anything of Sonoff's is written over; without it, Sonoff's official firmware for
  it, with the identity the device keeps or your eWeLink account holds
  ([how](../../docs/what-happens.md#changing-firmware-later)).
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../docs/adding-a-device.md)
  says what was needed.
