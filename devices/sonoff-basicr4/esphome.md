# ESPHome for the Sonoff BASICR4

[`esphome.yaml`](esphome.yaml) is the ESPHome the conversion installs when you
choose ESPHome. You do not need to build it: the tool installs it pre-built,
from [`artifacts/`](../../artifacts/README.md), and writes your Wi-Fi into it in
memory only.

## What it reports

It switches the relay, from its button and over the network. Sonoff's Magic
Switch, a wall switch read through the zero-cross input on GPIO5, is not
supported: none of ESPHome's own components reads it.

The relay is off after a first start, and otherwise as it was before a power
cut, unless you set its power-on state to `On` or `Off`. Its `Countdown`
switches it over after the seconds you set.

## Adopted into Home Assistant

Home Assistant's ESPHome builder can adopt a converted device from this config:
[how it works](../../docs/home-assistant-adoption.md).

## Changing it

Edit `esphome.yaml`, then build and publish it with
`python3 tools/repo/update_firmware.py esphome --device sonoff-basicr4`. Keep the
`WiFi Signal` sensor's and `Relay` switch's names, the `autoflash_wifi` action
and the two `AUTOFLASH-IMPRINT-` placeholders: the tool uses each
([how](../sonoff-s60/esphome.md#changing-it)).
