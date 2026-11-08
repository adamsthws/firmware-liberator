# ESPHome for the Sonoff Basic Gen5

[`esphome.yaml`](esphome.yaml) is the ESPHome the conversion installs on a
BASIC-1GS when you choose ESPHome, built on [LibreTiny](https://docs.libretiny.eu/)
for its BK7238. You do not need to build it: the tool installs the pre-built image
in [`../../artifacts/`](../../artifacts/README.md). Nothing is written into the
image: it reads your Wi-Fi from the stock store at boot, as the S61's does.

## What it reports

It is [the S61's config](../sonoff-s61/esphome.md) less the meter, with the pins
in [this device's page](README.md). It switches the relay from its two inputs and
over the network.

## Adopted into Home Assistant

Home Assistant's ESPHome builder can adopt a converted device from this config:
[how it works](../../docs/home-assistant-adoption.md).

## Changing it

Edit `esphome.yaml`, then build and publish it with
`python3 tools/repo/update_firmware.py esphome --plug sonoff-basic-1gs`. Keep the
`WiFi Signal` sensor's and `Relay` switch's names and the `autoflash_wifi` action:
the tool uses each.

ESPHome 2026.10.0 built the current image, `artifacts/esphome-sonoff-basic-1gs-2026.10.0-2.rbl`,
with `tools/repo/update_firmware.py esphome 2026.10.0 --plug sonoff-basic-1gs`.
