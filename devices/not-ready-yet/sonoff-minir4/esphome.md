# ESPHome for the Sonoff MINI Extreme

[`esphome.yaml`](esphome.yaml) is the ESPHome the conversion installs when you
choose ESPHome. You do not need to build it: the tool installs the pre-built image
in [`../../../artifacts/`](../../../artifacts/README.md) and writes your Wi-Fi into it,
in memory only.

It switches the relay, from its button, a wall switch and over the network.

## Adopted into Home Assistant

Home Assistant's ESPHome builder can adopt a converted device from this config:
[how it works](../../../docs/home-assistant-adoption.md).

## Changing it

Edit `esphome.yaml`, then build and publish it with
`python3 tools/repo/update_firmware.py esphome --plug sonoff-minir4`. Keep the
`WiFi Signal` sensor's and `Relay` switch's names, the `autoflash_wifi` action and
the two `AUTOFLASH-IMPRINT-` placeholders: the tool uses each
([the S60's page](../../sonoff-s60/esphome.md) says how).
