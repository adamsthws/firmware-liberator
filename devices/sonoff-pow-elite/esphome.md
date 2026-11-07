# ESPHome for the Sonoff POW Elite 16A

[`esphome.yaml`](esphome.yaml) is the ESPHome the conversion installs when you
choose ESPHome. You do not need to build it: the tool installs the pre-built image
in [`../../artifacts/`](../../artifacts/README.md) and writes your Wi-Fi into it,
in memory only.

## What it reports

It reports voltage, current, power and energy from the meter, uncalibrated, and
shows them on the display: a short press of the button switches the relay, a
longer one what the display shows. Over the `Overload threshold` (3680 W unless
you change it) for five seconds, it switches the relay off.

The relay is off after a first start, and otherwise as it was before a power cut.

## Adopted into Home Assistant

Home Assistant's ESPHome builder can adopt a converted device from this config:
[how it works](../../docs/home-assistant-adoption.md).

## Changing it

Edit `esphome.yaml`, then build and publish it with
`python3 tools/repo/update_firmware.py esphome --plug sonoff-pow-elite`. Keep the
`WiFi Signal` sensor's and `Relay` switch's names, the `autoflash_wifi` action and
the two `AUTOFLASH-IMPRINT-` placeholders: the tool uses each
([the S60's page](../sonoff-s60/esphome.md) says how).
