# Sonoff BASICR4

## Status

[![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-basicr4.json)](README.md)

## Testing

**Not proven yet:** no real one has been converted, and it does not run in the
emulator either. Its stock firmware will not start on the S60 flash the emulator
would run it on: it stops early (`espInit`: `ESP_ERR_NOT_FOUND`), looking up a
factory record the S60's settings do not carry. A real BASICR4 ships with its
own, so this is the stand-in flash's gap, not the device's.

**What is known** comes from eWeLink's official update file and published pin
maps: its firmware name, that its app loads at the offset the tool expects (the
S60's layout), and its pins. The conversion refuses a plug whose partition table
is not that one, before anything of Sonoff's is written over.

**Worth checking, on a real one:** what model it reports, whether its partition
table is the one the tool expects, and whether the relay, button and LED work.
Please report how it went, with the run's log.

## The device

Sonoff's ESP32-C3 inline switch, model **BASICR4**.

| | |
|---|---|
| Chip | ESP32-C3 |
| Firmware name | `FWSW-E32R4-BASICR4-ESP32C3` |
| Flash | 4 MB by the header of its official update file; nobody has read one here |
| Relay | GPIO4 |
| LED | GPIO6: lit high by the Tasmota template, lit low by the ESPHome sources |
| Button | GPIO9, active low |
| Zero-cross input | GPIO5: a pulse at each mains zero crossing, read for Sonoff's Magic Switch. Left unset here |
| Stock firmware | `SN-ESP32C3-BASICR4-01`, firmware name `FWSW-E32R4-BASICR4-ESP32C3`, 1.1.0 in the public file |
| Stock table | not known: the S60's is expected, and the run stops before anything is written if it is another |

The pins are [Blakadder's template](https://templates.blakadder.com/sonoff_BASICR4.html),
and agree with [ESPHome's device page](https://devices.esphome.io/devices/sonoff-basic-r4-v1.0),
taken from [digiblur's](https://digiblur.com/wiki/devices/relays/sonoff-basic-r4/), on
the relay, button and LED pins. They disagree on whether the LED is lit high or low;
this follows Blakadder's, which only changes when the LED is lit. GPIO5 is
[Tasmota's MagicSwitch input](https://github.com/arendst/Tasmota/pull/20247): the
device's zero-cross circuit drives it, so nothing here sets it as an output. Nobody
has checked the pins here, and the emulator cannot.

Blakadder's warning stands: **its circuit has no transformer, and its low-voltage side
sits at mains potential (130 V measured on 240 V mains). Never touch or connect its
pins with it on the mains.**

## Back to Sonoff

As from any Espressif device: the run backs up the device's whole flash, Sonoff's
firmware and settings, before anything is written over them, and going back writes
that backup again. Neither needs a public dump. The installer reaches the device through
Sonoff's own update method, in a file wrapped for `FWSW-E32R4-BASICR4-ESP32C3`, the
name the run also learns from eWeLink's offer or the public file list
([how](../../docs/adding-a-device.md#learning-the-firmware-name)); a wrong name is
refused by the device, which stays on Sonoff. With `--no-backup` there is no way
back: no Espressif device is sent Sonoff's official file.

## Files

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py), without MagicSwitch.
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../docs/adding-a-device.md)
  says what was needed.
