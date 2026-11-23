# Sonoff Dual R3

## Status

[![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-dualr3.json)](README.md)

## Testing

**In the emulator:** it converts from eWeLink's public firmware for it, put on
the TH Elite's flash, as no dump of a real one is published, with its own
settings in relay mode
([its work mode](../../docs/espressif-research.md#the-dual-r3s-work-mode)). The
emulator has none of its hardware but its CSE7761 meter, which ESPHome reads,
and its button and wall switches, which it presses: on Tasmota and on ESPHome
the button toggled relay 1, and S1 and S2 each toggled their own relay as
they closed and as they opened. The rest of its wiring below is what its
sources say, not what was seen.
Its firmware (1.6.1) reports its Wi-Fi MAC to the cloud (`staMac`) as 12 hex
digits with no separators, as the emulator's stand-in for eWeLink saw: the
tool finds the device on your network by that MAC, written either way.

**Worth checking:** whether both relays, the wall switches, the button and the
LED work, and which meter chip yours has.

## The device

Sonoff's two-channel inline switch with a power meter.

| | |
|---|---|
| Chip | ESP32 |
| Firmware name | `FWSWES-E32DR3-DUALR3-ESP32D0WDV3` |
| Flash | not known |
| Stock model | not known: eWeLink's product model alone says which device it is |
| Relays | GPIO27, GPIO14 |
| LED | GPIO13, inverted |
| Power meter | CSE7761: TX GPIO25, RX GPIO26, 38400 baud, even parity; a BL0930 on units since September 2021 |
| Button | GPIO0, active low |
| Wall switches | GPIO32, GPIO33 |

Pins from [Blakadder's template](https://templates.blakadder.com/sonoff_DUALR3.html),
whose warning stands: **its GPIOs are connected to AC power. Never connect
anything to its pins with it on the mains.**

## A motor

Sonoff's firmware runs a Dual R3 either as two relays or as a motor - Sonoff's
curtain motor mode, which drives a blind, an awning or a gate as well - as you
set it in the eWeLink app. In motor mode its two outputs drive the motor's two
windings, one to open and one to close, and both on at once can burn the motor
out. So the tool carries the mode across: a Dual R3 it finds in motor mode
comes out of the conversion as a cover, whichever firmware you choose, and its
two outputs are never on together - not from Home Assistant, the button, the
wall switches, nor at a start.

- **Where the mode comes from**: your device's Sonoff backup, which the tool
  takes before it writes anything over Sonoff's firmware (Sonoff keeps the mode
  as `deviceMode` in its settings), or, for a device already on Tasmota or
  ESPHome, how that firmware is set up as the run begins. Either one saying
  motor is enough. A Dual R3 an earlier release converted to two relays,
  though its backup says motor, is set up for its motor the next time you run
  the tool on it.
- **Tasmota**: its shutter, on both relays (`SetOption80 1`, `ShutterRelay1 1`),
  one to open and one to close (`ShutterMode 1`), the two relays interlocked
  (`Interlock 1,2`, `Interlock 1`), so that one switched on turns the other off
  first, and both off at power-on (`PowerOnState 0`). `TASMOTA_COMMANDS` in
  [`tools/autoflash/motor.py`](../../tools/autoflash/motor.py) is the list, and
  says why its order matters.
- **ESPHome**: its own build for a motor, [`esphome-motor.yaml`](esphome-motor.yaml),
  with a `Motor` cover in place of the two relays
  ([what it reports](esphome.md#a-motor)).
- **The travel times**: Sonoff's firmware times the motor's travel itself, by
  the motor's current, when you calibrate it in the app. A device calibrated
  there has its time carried across, the same each way, as Sonoff's firmware
  uses it. Otherwise both firmware start at `DEFAULT_TRAVEL` in `motor.py`, a
  few seconds each way, and the tool tells you to time your motor and set
  Tasmota's `ShutterOpenDuration1` and `ShutterCloseDuration1`, or ESPHome's
  `Open duration` and `Close duration` in Home Assistant. The default is short
  on purpose: too short only stops the motor early, where too long could drive
  a motor with no limit switches past its end. Until the times are right the
  cover's position is only a guess.
- **Direction**: output 1 (L1) opens and output 2 (L2) closes, unless you set
  the outputs to reverse in the app; then output 2 opens, and that is carried
  across too: on Tasmota as its template's two relays swapped (relay 1, which
  its shutter opens with, on output 2), on ESPHome as its `Reverse direction`
  setting, on. The interlock and both off at a start hold either way.
- **The wall switches**: they work as you set them in the app
  (`motorSwMode`, [as seen in the emulator](../../docs/espressif-research.md#the-dual-r3s-work-mode)),
  and that is carried across: push-buttons, the default, acting as one is
  let go - S1 opens, S2 closes, either stops the motor while it runs; hold
  to run, the motor moving while S1 or S2 alone is held; or one push-button
  on either input, opening, stopping, closing and stopping in turn. S1 and
  S2 swapped in the app (`motorKeyReverse`) stay swapped. On Tasmota the
  mode is its two switches' `SwitchMode` and a rule that has them give its
  shutter's own commands, and swapped switches are its template's two
  switches swapped; `TASMOTA_SWITCHES` in `motor.py` says which and why.
  The rule goes in the first of `Rule1` to `Rule3` that is free, or where the
  tool put it before; a rule of your own in any of them is never changed.
  The tool knows its rule by its text, any release's
  (`RULE_TEXTS` in `tasmota_rules.py`), so a rule an earlier release wrote is replaced
  where it is, never left running beside a new one.
  With all three holding rules of yours, the tool sets up the rest and leaves
  the switches as hold to run, says so and waits a few seconds before it
  carries on so you can read it (`RULE_WANTED_SHOWN`); its `run.log` says
  why. Empty one of them and run the tool again.
  Held together under hold to run, Tasmota runs the motor the way of the
  switch closed last, where Sonoff's firmware stops it. On ESPHome they are
  its `Wall switches` and `Swap wall switches` settings
  ([ESPHome's build](esphome.md#a-motor)).
- **From one of these firmware to the other**, the travel times, direction
  and wall switches it is set to go with the device, as its mode does.
- **The end-of-run check** does not switch the outputs, which would run the
  motor both ways; it checks the firmware is set up for the motor
  ([At the end](../../docs/what-happens.md#at-the-end)).
- **Back to Sonoff's firmware**: your backup puts the motor mode back with the
  rest of Sonoff's settings.

In the emulator a test copy set up in motor mode (`deviceMode` 2) went from
Sonoff's firmware to Tasmota and to ESPHome, and from each to the other
(`emulator_check.py sonoff-dualr3 --motor`), and so did one calibrated at 8
seconds with its outputs reversed (`--motor calibrated`). On each firmware the
travel times, direction and wall switches were what was carried across: from
Sonoff's backup, and on the move from Tasmota to ESPHome and from ESPHome to
Tasmota, the travel time, direction and wall switches the first firmware had
been set to, unlike the backup's (the check's `MOVED_TRAVEL`, the other way
round, the next mode). The outputs came
on one at a time, each way - asked to close while opening, the first went off
before the second came on - never both, even asked for both at once on
Tasmota, and both were off after a power cut: on ESPHome as the emulated meter
read each channel, on Tasmota as it said it drove its relays, each read while
the firmware said the motor was moving
([the check](../../docs/emulator.md#a-motor)). The wall switches worked as
Sonoff's firmware had them, on both firmware, in each mode, carried from the
backup and from the other firmware alike, swapped too: the calibrated copy
has them hold to run and swapped, a third copy one push-button
(`--motor one-button`). As push-buttons, each moved the motor only once let
go, S1 opening, S2 closing and either stopping it; held to run, each moved it
while held; as one push-button, each press went on from the last. The button
started the motor one way and stopped it. What the calibration, the
reversal and the wall-switch modes mean was seen in the emulator's Sonoff firmware
([research](../../docs/espressif-research.md#the-dual-r3s-work-mode)); how the
times suit a real motor, with its own limit switches, is for its owner to
check.

## Files

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml), and
  [`esphome-motor.yaml`](esphome-motor.yaml) for a device that drives a motor;
  [`esphome.md`](esphome.md) says what each reports.
- **Flash layout**: expected to be the TH Elite's, the layout a
  [real one's boot log](https://community.home-assistant.io/t/help-with-flashing-sonoff-dual-r3-please/297547?page=2)
  shows. If yours is laid out differently, the run stops once the installer has
  read its partition table, before anything is written over Sonoff's firmware,
  and switching it off and on takes it back to Sonoff's. Please report that,
  with its model. Leaving Tasmota, for ESPHome or for Sonoff's firmware, it
  spends a short time on a temporary layout table, as the TH Elite does: its
  first app slot leaves no room in front of Tasmota for the firmware that moves
  it on.
- **Calibration**: not carried over.
- **Back to Sonoff**: the tool writes back the device's own backup, taken before
  anything of Sonoff's is written over; without it, Sonoff's official firmware for
  it, with the identity the device keeps or your eWeLink account holds
  ([how](../../docs/what-happens.md#changing-firmware-later)).
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../docs/adding-a-device.md)
  says how a device is added.
