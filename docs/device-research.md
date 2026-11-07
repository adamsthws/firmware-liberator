# Device research

What is known about the devices the tool converts or might, and how it was found out,
so you need not repeat the work. To add a device, see
[adding-a-device.md](adding-a-device.md); what holds particular devices up is in
[devices/not-ready-yet/](../devices/not-ready-yet/README.md).

## By chip family

- [Espressif research](espressif-research.md): the ESP32 and ESP32-C3 devices, their
  partition layouts, a plug's Sonoff identity, locked devices, what a backup logs, and
  how a device's firmware name is learnt.
- [Beken research](beken-research.md): the eWeLink firmware on the BK7238 devices: its
  flash, update format, cloud, pairing, and the route to OpenBeken and ESPHome.

## By device

What was found out about one device is kept with its files, in its `research.md`, and
listed here:

- [Sonoff NSPanel](../devices/not-ready-yet/sonoff-nspanel/research.md): why its updater
  refuses the bridge's file.
- [Sonoff S61](../devices/sonoff-s61/research.md): its energy meter's calibration and
  its wiring.
- [Sonoff Basic Gen5](../devices/sonoff-basic-1gs/research.md): its flash size.

The emulators that most of it was found with are described in [emulator.md](emulator.md)
and [tools/beken/emulator/](../tools/beken/emulator/README.md).
