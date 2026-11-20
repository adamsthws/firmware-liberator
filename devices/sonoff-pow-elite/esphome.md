# ESPHome for the Sonoff POW Elite 16A

[`esphome.yaml`](esphome.yaml) is the ESPHome the conversion installs when you
choose ESPHome. You do not need to build it: the tool installs it pre-built,
from [`artifacts/`](../../artifacts/README.md), and writes your Wi-Fi into it in
memory only.

## What it reports

It switches the relay, and reports voltage, current, power and energy from the
meter, uncalibrated, and apparent and reactive power and the power factor,
worked out as the S60's are
([how](../sonoff-s60/esphome.md#what-it-reports)), which Home Assistant leaves
off until you turn them on. The display shows them: a short press of the button
switches the relay, and a longer one changes what the display shows. Over the
`Overload threshold` (3680 W unless you change it) for five seconds, it switches
the relay off, and the `Overload` sensor shows it. `Daily energy`, `Monthly
energy`, `Yearly energy` and `Total energy`, counted from the meter's energy,
are kept through restarts and updates; the first three start again each day,
month and year.

The relay is off after a first start, and otherwise as it was before a power
cut, unless you set its power-on state to `On` or `Off`. Its `Countdown`
switches it over after the seconds you set.

## Adopted into Home Assistant

Home Assistant's ESPHome builder can adopt a converted device from this config:
[how it works](../../docs/home-assistant-adoption.md).

## Changing it

Edit `esphome.yaml`, then build and publish it with
`python3 tools/repo/update_firmware.py esphome --device sonoff-pow-elite`. Keep the
`WiFi Signal` sensor's and `Relay` switch's names, the `autoflash_wifi` action
and the two `AUTOFLASH-IMPRINT-` placeholders: the tool uses each
([how](../sonoff-s60/esphome.md#changing-it)).
