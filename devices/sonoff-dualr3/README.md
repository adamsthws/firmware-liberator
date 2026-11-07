# Sonoff Dual R3

## Status

[![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-dualr3.json)](README.md)

## Testing

**In the emulator:** it converts from eWeLink's public firmware for it, put on the TH Elite's flash, as no dump of a real one is published. The emulator has none of its hardware, so its wiring below is what its sources say, not what was seen.

**Worth checking:** whether both relays, the wall switches, the button and the LED work, and which meter chip yours has.

## The device

Sonoff's two-channel inline switch with a power meter.

Its flash is expected to be laid out as the TH Elite's, the layout a
[real one's boot log](https://community.home-assistant.io/t/help-with-flashing-sonoff-dual-r3-please/297547?page=2)
shows. If yours is laid out differently, the run stops once the installer has read
its partition table, before anything is written over Sonoff's firmware: switching
it off and on goes back to Sonoff's. Please report that with its model. Leaving
Tasmota, for ESPHome or for Sonoff's firmware, it spends a short time on a
temporary layout table, as the TH Elite does, as its first app slot leaves no room
in front of Tasmota for the firmware that moves it on.

| | |
|---|---|
| Chip | ESP32 |
| Relays | GPIO27, GPIO14 |
| LED | GPIO13, inverted |
| Power meter | CSE7761: TX GPIO25, RX GPIO26, 38400 baud, even parity; a BL0930 on units since September 2021 |
| Button | GPIO0, active low |
| Wall switches | GPIO32, GPIO33 |

Pins from [Blakadder's template](https://templates.blakadder.com/sonoff_DUALR3.html), whose warning stands: **its GPIOs are connected to AC power. Never connect anything to its pins with it on the mains.**

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../docs/adding-a-device.md)
  says what was needed.
