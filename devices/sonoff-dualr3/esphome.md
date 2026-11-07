# ESPHome for the Sonoff Dual R3

[`esphome.yaml`](esphome.yaml) is the ESPHome the conversion installs when you
choose ESPHome. You do not need to build it: the tool installs the pre-built image
in [`../../artifacts/`](../../artifacts/README.md) and writes your Wi-Fi into it,
in memory only.

## What it reports

It switches both relays, from its button, wall switches and over the network, and reports voltage, currents and power from a CSE7761. Units with a BL0930 report no readings.

## Adopted into Home Assistant

Home Assistant's ESPHome builder can adopt a converted device from this config:
[how it works](../../docs/home-assistant-adoption.md).

## Changing it

Edit `esphome.yaml`, then build and publish it with
`python3 tools/repo/update_firmware.py esphome --plug sonoff-dualr3`. Keep the
`WiFi Signal` sensor's and `Relay` switch's names, the `autoflash_wifi` action and
the two `AUTOFLASH-IMPRINT-` placeholders: the tool uses each
([the S60's page](../sonoff-s60/esphome.md) says how).
