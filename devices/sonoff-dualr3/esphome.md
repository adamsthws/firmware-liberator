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

## A motor

A Dual R3 set up in Sonoff's firmware to drive a motor is given
[`esphome-motor.yaml`](esphome-motor.yaml) instead
([why](README.md#a-motor)). Its board is the same, and it reports the
same voltage, currents, power and energy, each channel's now the motor's each
way. In place of the two relays it has a `Motor` cover, which Home Assistant
opens, closes, stops and sets to a position. It has no device class, as the
motor may move a curtain, a blind, an awning or a gate: Home Assistant shows
a plain cover, and you can give it a class there.

- Output 1 opens and output 2 closes, or the other way round with `Reverse
  direction` on, a setting in Home Assistant that the tool turns on for a
  device whose outputs Sonoff's app had reversed; changing it stops the motor.
  Each output is interlocked with the other in the build itself: switching one
  on turns the other off and waits half a second for the motor to stop. Both
  are off at every start, whatever they were before, so the cover has no
  power-on state, nor countdown, nor overload threshold (the motor draws far
  less than an output's rating).
- The button opens, stops and closes in turn. The wall switches work as
  `Wall switches` says, a setting in Home Assistant that the tool sets as
  Sonoff's app had them: `Push-buttons` (Sonoff's default), acting as one is
  let go - S1 opens, S2 closes, either stops the motor while it runs; `Hold
  to run`, the motor moving while S1 or S2 alone is held; or `One
  push-button`, either one opening, stopping, closing and stopping in turn.
  `Swap wall switches` on has S2 open and S1 close.
- `Open duration` and `Close duration`, settings in Home Assistant, are how
  long your motor takes each way: the time Sonoff's calibration found, which
  the tool carries across, or a few seconds (`DEFAULT_TRAVEL` in
  [`motor.py`](../../tools/autoflash/motor.py)) until you set them. The cover's
  position is worked out from them.
