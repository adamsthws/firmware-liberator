# Sonoff TH Elite 20A

Sonoff's the **THR320D**: a thermostat switch with a socket for a sensor and a display. Its stock firmware calls itself `SN-ESP32D0-THR3-01`, and so does the THR316D's, wired otherwise: the tool converts one eWeLink calls a THR320D, and no other.

**Tested in the emulator only.** The conversion has run from a public dump of a
stock one; no real one has been converted. The emulator has none of its hardware
to see, so the wiring below is what its sources say.

| | |
|---|---|
| Chip | ESP32 (4 MB flash) |
| Main relay | latching: a pulse on GPIO19 turns it on, on GPIO22 off. **Both on together overheats the device** |
| Dry contact | GPIO4 |
| LEDs | GPIO16, GPIO15, GPIO13, inverted |
| Display | TM1621: CS GPIO17, data GPIO5, read GPIO23, write GPIO18 |
| Sensor socket | data GPIO25, power GPIO27 |
| Button | GPIO0, active low |

Pins from [ESPHome's page](https://devices.esphome.io/devices/sonoff-thr320d/) and [Blakadder's template](https://templates.blakadder.com/sonoff_THR320D.html), which agree.

- **Tasmota**: the official release, unchanged, given the template in
  [`tools/autoflash/kinds.py`](../../tools/autoflash/kinds.py).
- **ESPHome**: [`esphome.yaml`](esphome.yaml); [`esphome.md`](esphome.md) says what it reports.
- **Flash layout**: its own ([`artifacts/sonoff-th-elite-partition-table.bin`](../../artifacts/README.md)), with smaller app slots, so it runs a smaller Tasmota part way through. Tasmota's layout leaves no room in front of it for the small firmware that moves a plug off it, so **a TH Elite on Tasmota cannot be taken to ESPHome by this tool**. Choose before you convert.
- **Calibration**: not carried over.
- **How it is converted**: as the S60 is ([`docs/what-happens.md`](../../docs/what-happens.md)),
  with the firmware for its chip. [`docs/adding-a-device.md`](../../docs/adding-a-device.md)
  says what was needed.
