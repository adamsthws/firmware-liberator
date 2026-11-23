# Published firmware artifacts

The files the tool installs or reads, each pinned in [`SHA256SUMS`](SHA256SUMS)
(`sha256sum -c SHA256SUMS` here) and in [`firmware.json`](firmware.json).
Per-device credentials and flash captures belong in the Git-ignored
`captures/`, never here. [`../NOTICES.md`](../NOTICES.md) gives each
third-party file's source and licence, and
[`../docs/updating-firmware.md`](../docs/updating-firmware.md) how each file
is rebuilt and named. Earlier files are in `git log --all -- artifacts/`.

## The files

| File | Purpose | SHA-256 |
|---|---|---|
| `autoflash-bridge-esp32c3-4.4.7.ota` | [The bridge](#the-bridge) for an ESP32-C3, wrapped for the stock updater with the S60's model | `ad17d6de7f451874670352a014e7f5e66721c7e33e62bce2fede925a27dbea88` |
| `autoflash-bridge-esp32-4.4.7.bin` | The bridge for a classic ESP32, as a plain app the run wraps for each device's model | `6e6ecb8f37799fecf26c33c2100b38d2a704a24f2fc15cdd9c74dbbc82a7be19` |
| `tasmota32c3-bluetooth.bin` | [The Berry-capable Tasmota](#the-interim-tasmota-and-its-settings-templates) an ESP32-C3 runs for a while on its original partition layout: the exact file the successful migrations used, from Tasmota's moving `install/firmware` branch ([source](../NOTICES.md#tasmota-derived-firmware)) | `a92cfadaff5a3c824f790471ba31464acd2076a24ff1fcad2de28574c5641708` |
| `tasmota-settings-template-15.6.0.0.bin` | Tasmota's default settings with no Wi-Fi in them, for the bridge to fill in at D1 on an ESP32-C3 | `d63f03cb99540407037f36dc8155ec9b02d24425d3b02c52299713233e98f4a5` |
| `tasmota-settings-template-esp32-15.6.0.0.bin` | The same for a classic ESP32 | `7c790d6d445756cf7124841136a676741fbad33020706a8dd477e3dc51fbef49` |
| `autoflash-recovery-safeboot-esp32c3-imprintable.bin` | [The recovery Safeboot](#the-recovery-safeboot) for an ESP32-C3, with no credentials in it; it saves the Wi-Fi for the official Tasmota installed after it | `2fd4a50c58ed5619f788613cf89b894487059683df40b9f86d5b5c68666f1207` |
| `autoflash-recovery-safeboot-esp32-imprintable.bin` | The same for a classic ESP32 | `b6398e14d9d81155763fc61f5d2464f9e8f4c204e917fd47fff27161181edf4d` |
| `autoflash-recovery-safeboot-esp32c3-imprintable-ece36fd964c1.bin` | The ESP32-C3's recovery Safeboot before this one, as releases up to 3.1.0 published it ([kept](#the-recovery-safeboot) for runs they began) | `ece36fd964c1443ef9fb74af87a0a257501c9e5ecfb1fb7a1c3aff1744bc29fd` |
| `autoflash-recovery-safeboot-esp32-imprintable-f1a25251f775.bin` | The same for a classic ESP32 | `f1a25251f7756303e3d65061f9429d1f4da81968b89737d3d5d7364ea959860c` |
| `autoflash-mover-esp32c3-1.3.0.bin` | [The mover](#the-mover) for an ESP32-C3, a small ESPHome the run uses for a minute or two when a device leaves Tasmota | `d0b85963917624053a8a529ee42cccce54a0a2542c562274693a2f554f5e3243` |
| `autoflash-mover-esp32-1.2.0.bin` | The mover for a classic ESP32 | `9ae262cb5bb0d1b8669f9864322882d0f1b198d943c862c415b9af0661428c52` |
| `esphome-sonoff-s60-bootloader-1.2.0.bin` | [ESPHome's bootloader](#esphome-for-espressif-devices) for an ESP32-C3: it replaces Sonoff's when ESPHome is installed, so ESPHome can roll back an update that fails to start | `3792bb665916d69c410c9fd10f69d1a6968e61f2a9e4969829a1c49f9ea6f433` |
| `esphome-esp32-bootloader-1.2.0.bin` | ESPHome's bootloader for a classic ESP32 | `b52ccf9f4602c83db673d44e84d391c44fed6d24329d4fefb9fc0d72a95a0d4f` |
| `esphome-sonoff-s60-imprintable-2026.10.0-4.bin` | [ESPHome for the S60](#esphome-for-espressif-devices), installed instead of the final Tasmota when ESPHome is chosen, with placeholders for the device's Wi-Fi | `5472c9da167bb962778b1e1e1dda7459f98ff96b7fe0349b06d8c566ca003259` |
| `esphome-sonoff-pow-elite-imprintable-2026.10.0-4.bin` | ESPHome for the POW Elite, the same way | `c5f0069823b8f5fa686856afcd9b89c0fa5ceb6f28d31512d0c6e2f4403b9471` |
| `esphome-sonoff-powct-imprintable-2026.10.0-5.bin` | ESPHome for the POW Ring (POWCT), the same way | `561bb066b6c9bd6eb3415b67076753722a0f3f7a1d64974be2c613e13fec6cad` |
| `esphome-sonoff-th-elite-imprintable-2026.10.0-4.bin` | ESPHome for the TH Elite, the same way | `8a8b11574c041d057e66a788de94cc42d2628abf25695fcf4879f94ab9a3ccdf` |
| `esphome-sonoff-dualr3-imprintable-2026.10.0-4.bin` | ESPHome for the Dual R3, the same way | `bbb99356be084f6a7e93f55aa2e92cd71c942f1f8ac8fbb4cd8b7ffa101dc5bb` |
| `esphome-sonoff-dualr3-motor-imprintable-2026.10.0-4.bin` | ESPHome for a Dual R3 that drives a motor, from its `esphome-motor.yaml` | `7c21392de73225300a62400bf119330df0c19c012c0e63222d7bf79e3f38be53` |
| `sonoff-s60-partition-table.bin` | [Sonoff's partition table](#sonoffs-partition-tables) of the S60, and of every device on its layout | `f63f66bbf23b9e291c7eb5dcf24be820190dacf4bf52af515d9664526a4f4daf` |
| `sonoff-nspanel-partition-table.bin` | Sonoff's partition table of the NSPanel: the S60's without its unused `reserve` entry | `3c6436bcbd529015fcb3fd98565b7753199910b3dba30c9d6ccce77d08e09d70` |
| `sonoff-th-elite-partition-table.bin` | Sonoff's partition table of the TH Elite, with a larger NVS and smaller app slots | `36d7dc76c5d2459ea174e48aaa19ff93dcb8130c43b124bba409d78b9c01c1b6` |
| `sonoff-basicr4-partition-table.bin` | Sonoff's partition table of the BASICR4, Espressif's Matter SDK's, which its firmware reads at 0xC000 | `9f77593124a323cc4e5fad5e17a8197c67d3dca7b02ac3a51a6f16be5d421ac9` |
| `openbeken-sonoff-s61-1.18.320-2.rbl` | [OpenBeken for the S61](#beken-devices), with the 4 MB table, the hook before an OTA's restart, the tool's OpenBeken driver and the S61's table for it; a run to ESPHome sends it as the interim firmware, with a marker for its app after it | `4faef4259545557d2eee7e12499cb24c89c51e443cb987d7ad90d5f7d0bf68c1` |
| `esphome-sonoff-s61-2026.10.0-4.rbl` | ESPHome for the S61, on LibreTiny. Nothing is written into it: its first boot reads the device's Wi-Fi and meter calibration from the stock store | `583ce81572f81be40882765bcdf07be0f03dbaa1f0c81dfa9f130861d05ed091` |
| `openbeken-sonoff-basic-1gs-1.18.320-1.rbl` | OpenBeken for the Basic Gen5, built as the S61's with that device's table for the driver | `5129951d1e89b06f4c5c7e39a0d01b081010d338c562d78c6e422f7175965952` |
| `esphome-sonoff-basic-1gs-2026.10.0-3.rbl` | ESPHome for the Basic Gen5, built as the S61's less its meter. No real one has been converted | `7de065a559b8814d9d064764040c4ff97d8e415e881b828f7fa5cee1f35abcc9` |
| `openbeken-readout-1.18.320-1.rbl` | OpenBeken for a [readout](../docs/readouts.md) of a Beken device the tool does not convert yet (`tools/autoflash/readout.py`): the S61's, with a table that sets no pins, so nothing is driven on hardware nobody has mapped. It joins the device's Wi-Fi, serves the whole flash to be read, and takes Sonoff's firmware back | `811a8437129eea92b4c29c60e5c56e623fd32ca7b1629625d91e295a901bd572` |

## The bridge

The bridge is the one-shot firmware the stock updater installs. It joins the
home Wi-Fi the stock firmware was on, lets the run back up the whole flash,
gives Tasmota its settings, and restarts the device into Sonoff's firmware on
`POST /restart`. It starts even after an upload into Sonoff's slot was cut off
part way, and its status page then says `fallback: none`. Failing to join, it
raises its own open network, `autoflash-recovery-XXXX` (the last two bytes of
the device's Wi-Fi MAC), and tries the home one again every three minutes
while nobody is joined to it. On a device whose partition table is at 0xC000
(the BASICR4's) it copies that table to 0x8000 before it takes Tasmota, so
that Tasmota starts. [`../archive/bridge/README.md`](../archive/bridge/README.md) has its
source and endpoints. 4.4.6 and 4.4.7 have been tested in the emulator, and
bridges up to 4.4.5 on real devices too.

Its code is the same for every device; the wrapper names the model. A bridge
has one version, in its wrapper, its app description and its status page
(`bridge 4.4.7`); `tools/espressif/build_vendor_ota.py` refuses any other. The
next is 4.4.8. The stock updater is told 9.9.9 (`ANNOUNCED_VERSION`), as the
S61's images are sent. The stock 1.2.0 updater does not compare versions (a
device on 1.2.0 took bridge v5 wrapped as `9.9.9`), but a later Sonoff
firmware might refuse anything older than itself, so the version must stay
above every Sonoff release, in at most seven characters.
`tools/repo/update_firmware.py esp32c3 bridge` and `esp32 bridge` build and
publish each chip's. Up to 4.4.5 the file named the S60
(`autoflash-bridge-sonoff-s60-*.ota`); the run still recognises a device left
on an earlier bridge ([old names](../docs/device-names.md#old-names)).

## The interim Tasmota and its settings templates

A classic ESP32's interim Tasmota is not kept here: the run downloads it from
Tasmota's release, as `firmware.json` pins it. Each settings template is its interim Tasmota's defaults, captured from a
converted device with `tools/espressif/capture_tasmota_template.py` and tested
on one with `--verify` (the classic ESP32's through `update_firmware.py esp32
template`). Its Wi-Fi names, passwords, last access point and addresses are
blank or Tasmota's defaults. It fits only that Tasmota (the ESP32-C3's Berry
build is 15.6.0.1, the same settings layout as 15.6.0); if that, or the
Tasmota the interim firmware is made from (15.6.0), changes, capture it again
(`tools/autoflash/tasmota_settings.py`). The official release a device ends on
can change without it. The ESP32-C3's next is named
`tasmota-settings-template-esp32c3-<version>.bin` (`template_path`).

## The recovery Safeboot

It is built from the pinned Tasmota source with
`tools/espressif/safeboot/repartition/recovery-safeboot/volatile-wifi-defaults.patch`
and `persist-settings.patch`. On its first boot after the repartition it wipes
NVS (by then only what Sonoff's firmware left and part of an earlier Tasmota
record), saves its settings with the Wi-Fi where Tasmota reads them, and reads
them back, so the official Tasmota installed next joins by itself. It logs
whether that worked, which the run records at F2, and never touches settings
already there. The record holds only what a Safeboot sets, so F4 starts with
`Reset 4`, which rebuilds the rest from official Tasmota's defaults and keeps
the Wi-Fi. It goes by `autoflash-recovery-XXXX` and saves that name, so the
Tasmota after it keeps it until `Reset 4`. Its log lines start "Autoflash
recovery"; the run still reads those of the earlier
`s60-recovery-safeboot-imprintable.bin` ("S60 recovery", `s60-recovery-NNNN`).

It holds placeholder bytes instead of real Wi-Fi credentials and **must not be
flashed as published**: the private bundle-preparation path must imprint and
validate a per-device copy first.

Its Wi-Fi is kept out of modem sleep (`WIFI_NO_SLEEP`, `SetOption127`'s
default, in its header), for the upload of official Tasmota it takes, and the
record it saves carries that to official Tasmota
([why](../docs/technical-details.md#the-wi-fi-radio-kept-awake)).

`update_firmware.py esp32c3 recovery` and `esp32 recovery` build and publish
each chip's, pinned in `firmware.json`: the header with the placeholders from
`tools/espressif/safeboot/configure_imprintable_safeboot.py`, the build's paths
blanked, and `validate_imprintable_safeboot.py` saying it is publishable. A
rebuild matches the published file but for its build date and time; built in
a directory whose path is not 38 characters long, everything after the first
such path is a few bytes along. The one a build replaces, if a release
published it, is kept as `autoflash-recovery-safeboot-<chip>-imprintable-<sha>.bin`
under the chip's `recovery` `earlier` in `firmware.json`: a run that release
began has it on the device, and G1 proves the run's Wi-Fi by it before the
official Safeboot goes over it.

## The mover

The mover is a small ESPHome that fits in front of Tasmota in Sonoff's layout and takes the
next firmware as an ordinary ESPHome update. Two minutes away from the home
Wi-Fi it raises its own network, whose page takes new Wi-Fi details.
`GET /mover` tells the run it is the mover while its update service is busy or
stuck, and `POST /restart` restarts it without anyone's help. It drives no
pins, so one build serves every device with its chip and 4 MB of flash. It
goes by `autoflash-recovery-XXXX` and holds Wi-Fi placeholders. Each chip's is
built from `devices/<chip>/mover.yaml` ([the ESP32-C3's](../devices/esp32c3/mover.yaml))
with the ESPHome release `firmware.json` records.

## ESPHome for Espressif devices

The S60's image is built from
[`../devices/sonoff-s60/esphome.yaml`](../devices/sonoff-s60/esphome.yaml)
(its `project` version is the one in the file name) with ESPHome 2026.10.0 and
ESP-IDF 5.5.6, as `esphome compile sonoff_s60.yaml`, in ESPHome's own Docker
image for that release (`firmware.json` pins it by digest), and the file
published is `firmware.ota.bin`. Every other Espressif device's is built the
same way from its own `esphome.yaml`. `2026.10.0-1` would be this project's
first build on ESPHome 2026.10.0; OpenBeken's are numbered alike. To publish a
new one, change the config and run `tools/repo/update_firmware.py esphome`.

Each image holds `AUTOFLASH-IMPRINT-` placeholders for the Wi-Fi, which the
run fills in a copy in memory (`tools/autoflash/imprint.py`); it still
recognises the `S60-IMPRINT-` placeholders and `s60_wifi` action of earlier
builds. Like ESPHome's ready-made images, it has no OTA password, web login or
API encryption key: your device is open on your home network until you add
them, as official Tasmota is. What the S60's reports, its calibration and its
names are in [`../devices/sonoff-s60/esphome.md`](../devices/sonoff-s60/esphome.md).

The ESP32-C3's bootloader is `bootloader/bootloader.bin` from the build of the
S60's config at 1.2.0, with ESPHome 2026.9.1 and ESP-IDF 5.5.5; the classic
ESP32's is from its mover's build. A rebuilt image keeps the published
bootloader, so no device is given a new one.

## Sonoff's partition tables

Each is the standard ESP-IDF partition table a model ships with: partition
names, offsets and sizes, with no code, settings or secrets
([why they are kept](../docs/legal.md)). The S60's was read from a stock
device: every conversion on its layout starts from it, the Tasmota route's
preflight requires it byte for byte, and it is written back when a device
leaves Tasmota's layout. `update_firmware.py esp32 tables` takes the others
from public flash dumps (`tools/espressif/emulator/public_dump.py`), and the
BASICR4's, of which none is published, as a real one's is, byte for byte
(`BASICR4_TABLE` there, the emulator's copy of it), checked against the
hashes in `tools/espressif/layouts.py`.

## Beken devices

The Beken images are rt_ota images (`.rbl`), which the stock bootloader
applies from its download slot, sent by the stock updater, OpenBeken's own OTA
or in a UF2 through ESPHome's. OpenBeken is OpenBK7231T_App at the commit
`firmware.json` pins (its release is in the version; `NOTICES.md` names both
commits) with this project's 4 MB-table patches and driver
(`tools/beken/openbeken/`) and the device's table for it (the S61's is
`devices/sonoff-s61/drv_autoflash_sonoff_s61.c`), built as `OpenBK7238` with
`FLASH_SIZE=4M` in `tools/beken/openbeken/build.Dockerfile`. ESPHome for the
S61 is [`../devices/sonoff-s61/esphome.yaml`](../devices/sonoff-s61/esphome.yaml)
built with ESPHome 2026.10.0 on LibreTiny 1.13.0, `image_bk7238_app.ota.rbl`
of its build. `firmware.json` pins both under `beken`, with the commits,
hashes and build images. `tools/repo/update_firmware.py openbeken` and
`esphome --device sonoff-s61` build and publish them, and `--device
sonoff-basic-1gs` the Basic Gen5's.

OpenBeken names the device `openbeken-abcdef` (its hostnames too), ESPHome
`esphome-abcdef` with the comment `Sonoff-S61`. ESPHome has no backup reader,
so the tool takes the backup through OpenBeken, the interim firmware, which
knows itself by the marker sent after it. ESPHome is adopted into Home
Assistant as a package, not a copy of its config.
