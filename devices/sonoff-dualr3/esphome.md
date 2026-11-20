# ESPHome for the Sonoff Dual R3

[`esphome.yaml`](esphome.yaml) is the ESPHome the conversion installs when you
choose ESPHome. You do not need to build it: the tool installs it pre-built,
from [`artifacts/`](../../artifacts/README.md), and writes your Wi-Fi into it in
memory only.

## What it reports

It switches both relays, from its button, its wall switches and over the
network, and reports voltage, each channel's current and power from a CSE7761,
and each channel's apparent and reactive power and power factor (`Apparent
power 1`, `Power factor 2` ...), worked out as the S60's are
([how](../sonoff-s60/esphome.md#what-it-reports)), which Home Assistant leaves
off until you turn them on.
Units with a BL0930 report no readings. Each channel keeps its own energy totals
(`Daily energy 1`, `Monthly energy 2` ...), counted from its power, and switches
its own relay off after five seconds over its `Overload threshold 1` or `2`
(2200 W, the channel's rating, unless you change it).

Each relay is off after a first start, and otherwise as it was before a power
cut, unless you set its power-on state to `On` or `Off`. Each relay's countdown
(`Countdown`, `Countdown 2` ...) switches it over after the seconds you set.

## Adopted into Home Assistant

Home Assistant's ESPHome builder can adopt a converted device from this config:
[how it works](../../docs/home-assistant-adoption.md).

## Changing it

Edit `esphome.yaml` or `esphome-motor.yaml`, then build and publish both with
`python3 tools/repo/update_firmware.py esphome --device sonoff-dualr3`. Keep
their boards alike: `tests/repo/test_esphome_shared.py` holds the motor's to
`esphome.yaml`'s. Keep the
`WiFi Signal` sensor's and `Relay` switch's names, the `autoflash_wifi` action
and the two `AUTOFLASH-IMPRINT-` placeholders: the tool uses each
([how](../sonoff-s60/esphome.md#changing-it)).

## A curtain motor

A Dual R3 set up in Sonoff's firmware to drive a curtain motor is given
[`esphome-motor.yaml`](esphome-motor.yaml) instead
([why](README.md#a-curtain-motor)). Its board is the same, and it reports the
same voltage, currents, power and energy, each channel's now the motor's each
way. In place of the two relays it has a `Curtain` cover, which Home Assistant
opens, closes, stops and sets to a position:

- Output 1 opens and output 2 closes. Each is interlocked with the other in
  the build itself: switching one on turns the other off and waits half a
  second for the motor to stop. Both are off at every start, whatever they were
  before, so the cover has no power-on state, nor countdown, nor overload
  threshold (the motor draws far less than an output's rating).
- The button opens, stops and closes in turn; wall switch 1 opens and wall
  switch 2 closes, each stopping the motor if it is moving that way already.
- `Open duration` and `Close duration`, settings in Home Assistant, are how
  long your motor takes each way: 10 seconds until you set them. The cover's
  position is worked out from them.
