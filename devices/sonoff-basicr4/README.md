# Sonoff BASICR4

## Status

[![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-basicr4.json)](README.md)

## Testing

**In the emulator:** it converts to Tasmota and to ESPHome, each then to the
other, and from Tasmota back to Sonoff's firmware. There its official update
file runs on the S60's flash laid out on a real BASICR4's partition table, as
no whole dump of one is published. The emulator has none of its hardware but
its button, so its wiring below is what its sources say, not what was seen.

**Worth checking:** whether the relay, button and LED work, and that the
conversion found its table where its firmware reads it (`Partition table at 0xc000`
in the run's log).

**A readout is safe** ([`docs/readouts.md`](../../docs/readouts.md)): the
installer (bridge 4.4.6 or later) takes the table from 0xC000 and writes
nothing; the copy sent in has the Wi-Fi password and eWeLink identity taken out
of every settings partition, and of the records this firmware keeps them in
(its pair and factory records). A readout of a real one has shown its whole
flash layout ([below](#files)).

## The device

Sonoff's ESP32-C3 inline switch, model **BASICR4**.

| | |
|---|---|
| Chip | ESP32-C3: revision 0.4 on the one read out |
| Firmware name | `FWSW-E32R4-BASICR4-ESP32C3` |
| Flash | 4 MB: flash id 0x204016 on the one read out, as the header of its official update file says |
| Stock model | `SN-ESP32C3-BASICR4-01`, 1.1.0 in the public file |
| Relay | GPIO4 |
| LED | GPIO6: lit high by the Tasmota template, lit low by the ESPHome sources |
| Button | GPIO9, active low |
| Zero-cross input | GPIO5: a pulse at each mains zero crossing, read for Sonoff's Magic Switch. Left unset here |
| Thermistor | GPIO3 (ADC1 channel 3), read by its stock firmware: under 580 mV it takes itself to be overheating and refuses updates. Not seen on a board yet, and read by neither the Tasmota template nor the ESPHome config here, so a converted one would lose this protection |

Pins from [Blakadder's template](https://templates.blakadder.com/sonoff_BASICR4.html),
which agrees on the relay, button and LED pins with
[ESPHome's device page](https://devices.esphome.io/devices/sonoff-basic-r4-v1.0),
taken from [digiblur's](https://digiblur.com/wiki/devices/relays/sonoff-basic-r4/).
They disagree on whether the LED is lit high or low; this follows Blakadder's,
and the choice changes only when the LED is lit. GPIO5 is
[Tasmota's MagicSwitch input](https://github.com/arendst/Tasmota/pull/20247): the
device's zero-cross circuit drives it, so nothing here sets it as an output.
Nobody has checked the pins here, and the emulator cannot.

Blakadder's warning stands: **its circuit has no transformer, and its low-voltage
side sits at mains potential (130 V measured on 240 V mains). Never touch or
connect its pins with it on the mains.**

## Files

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py), without MagicSwitch.
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says
  what it reports.
- **Flash layout**: read from a real one. Its bootloader ends at 0x4D70,
  0x8000-0xBFFF is blank (erased), and its partition table is at 0xC000:

  | Partition | Type, subtype | Offset | Size | On the one read out |
  |---|---|---|---|---|
  | `esp_secure_cert` | 0x3f, 0x06 | 0xD000 | 0x2000 | blank |
  | `nvs_matter` | data, nvs | 0x10000 | 0x6000 | empty |
  | `nvs_matter_bkp` | data, nvs | 0x16000 | 0x6000 | empty |
  | `nvs` | data, nvs | 0x1C000 | 0x10000 | all of Sonoff's settings, its pair and factory records among them, and ESP-IDF's Wi-Fi settings |
  | `otadata` | data, ota | 0x2C000 | 0x2000 | |
  | `phy_init` | data, phy | 0x2E000 | 0x1000 | |
  | `ota_0` | app, ota_0 | 0x30000 | 0x1E0000 | |
  | `ota_1` | app, ota_1 | 0x210000 | 0x1E0000 | |
  | `fctry` | data, nvs | 0x3F0000 | 0x6000 | empty |

  It is the layout of Espressif's Matter SDK, but its Matter partitions are
  unused: the firmware keeps everything in `nvs`. `esp_secure_cert` is marked
  encrypted (flags 1), though flash encryption is off: ESP-IDF, 4.4 and 5.x
  alike, treats no partition as encrypted then, so the mark changes nothing
  for Sonoff's firmware or what the tool installs. The tool's copy of the table
  keeps it, as the device's own does, and so does the table a conversion
  commits on (the interim table); the temporary one leaves `esp_secure_cert`
  out. The tool knows it by
  [`artifacts/sonoff-basicr4-partition-table.bin`](../../artifacts/README.md)
  (`sonoff-basicr4` in [`layouts.py`](../../tools/espressif/layouts.py)). In the emulator,
  [`public_dump.py`](../../tools/espressif/emulator/public_dump.py) lays the
  S60's flash out on this table, with eWeLink's official update file in
  `ota_0`, the factory and pair records a paired one has, and its thermistor
  reading room temperature, so that it takes updates.
- **Calibration**: none, as it has no meter.
- **Back to Sonoff**: the tool writes back the device's own backup, taken before
  anything of Sonoff's is written over, its table at 0xC000 too. Without the
  backup it cannot: its firmware keeps its eWeLink identity in records of its
  own, which the tool keeps nowhere else, so keep the backup the run makes.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the official Tasmota and ESPHome, unchanged, but for its table. Before
  Tasmota goes on, the installer copies the table to 0x8000, where Tasmota,
  ESPHome and their bootloader read it, and which Sonoff's bootloader and
  firmware never read: a power cut at any point leaves it starting Sonoff's
  firmware or the installer, as on any device. From there it is converted as
  any device is, and leaving Tasmota it spends a short time on a temporary
  table, as the TH Elite does
  ([research](../../docs/espressif-research.md#converting-a-table-at-0xc000)).
  [`docs/adding-a-device.md`](../../docs/adding-a-device.md) says how a device is added.
