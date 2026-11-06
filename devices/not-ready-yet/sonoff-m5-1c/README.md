# Sonoff M5 1-gang

**Not ready yet: the tool does not convert this device.** its stock firmware starts in the emulator, run on another plug's flash as no usable dump of one is published (the public one is of a locked Matter version), but goes quiet and never starts its Wi-Fi. See [what is holding each one up](../README.md).

Sonoff's the SwitchMan wall switch with 1 gang. **Not the Matter version**, whose part number ends in W: it has secure boot and flash encryption on, and cannot be converted; the tool does not offer it.

| | |
|---|---|
| Chip | ESP32 |
| Relays | GPIO23 |
| Status LED | GPIO5, inverted |
| Buttons | GPIO0 |

Pins from [ESPHome's page](https://devices.esphome.io/devices/sonoff-m5/); Blakadder's template for this firmware's pins was not used, so the Tasmota template in [`kinds.py`](../../../tools/autoflash/kinds.py) was worked out from that table.

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../../docs/adding-a-device.md)
  says what was needed.
