# Updating the firmware the tool installs

A converted plug ends on official Tasmota, OpenBeken or this project's
ESPHome. Tasmota and ESPHome follow their projects' releases, a little
behind: the repository's `firmware-update` workflow takes each once it has
settled, tests it and releases it, by itself ([below](#by-itself)). Doing it
by hand takes the same steps ([after that](#by-hand)).

## What follows a release, and what does not

| Firmware | On the plug | Follows | Pinned in |
|---|---|---|---|
| Official Tasmota: `tasmota32c3.bin`, `tasmota32c3-safeboot.bin` (and `tasmota32c3.factory.bin`, for its partition table); the classic ESP32's `tasmota32*.bin`, at the same release | for good | every Tasmota release | [`firmware.json`](../artifacts/firmware.json) |
| ESPHome for each device (`artifacts/esphome-*`) | for good | every ESPHome release, every device's built together (`esphome --every-device`) | `firmware.json` |
| ESPHome's bootloader (`artifacts/esphome-sonoff-s60-bootloader-*.bin`) | for good | only when it has to change | `firmware.json` |
| The mover (`artifacts/autoflash-mover-*.bin`) | a minute or two | only when its config changes | `firmware.json` |
| OpenBeken for each Beken device (`artifacts/openbeken-*.rbl`) | for good | every OpenBeken release, every Beken device's built together (`openbeken --every-device`), its six scenarios run | `firmware.json` |
| The Berry Tasmota, the recovery Safeboot, the settings template, the bridge | minutes | no | [`artifacts/README.md`](../artifacts/README.md) says where, and how each is changed |

A new pin reaches plugs already converted. Running the tool on a plug with an
earlier release of its firmware offers this one as an update, and the plug
takes it, so treat a pin bump as a change to plugs in use as well as to new
conversions ([Changing firmware later](what-happens.md#changing-firmware-later)).
A plug adopted into Home Assistant keeps to the release it names, and the tool
leaves it alone: `firmware.json` records when each published ESPHome image was
built (`built`, `earlier_builds`), and the tool updates only a plug that reports
one of those times.

The official Tasmota files are not kept in this repository: each run downloads
them from the release's own page on GitHub and checks them against
`firmware.json`. The ESPHome builds are kept in it: the run writes the plug's
Wi-Fi into a copy of each.

**The interim firmware** - everything a plug runs only part way through a
conversion - stays as it is. Each has been through every fault the suite puts
in its way, a change to one means all of that again, and none of it is left
on a plug. The Tasmota they are made from is `INTERIM_TASMOTA_COMMIT` in
`tools/espressif/safeboot/safeboot_migration.py`, which need not be the official release's: the
recovery Safeboot saves the Wi-Fi in its release's settings record, and the
official release takes that record and moves it on to its own layout, as
Tasmota does on any upgrade - as long as `CFG_HOLDER` is the same in both,
which the update checks. The S61's interim firmware is its OpenBeken image
itself, with a marker sent after it, so it changes with every OpenBeken
update ([below](#3-openbeken-and-esphome-for-the-s61)).

**The bridge** changes only when its source in `archive/bridge` does, as a new
version. `python3 tools/repo/update_firmware.py esp32c3 bridge` publishes the
ESP32-C3's, wrapped for the S60's stock updater, and `esp32 bridge` the classic
ESP32's. Each builds in Docker, and replaces the file before it in `artifacts/`.

**The bootloader** is written in about a second, and a power cut in that
second leaves a plug that has to be opened - the only step of a conversion
that can. So a new ESPHome release brings a new app, and the published
bootloader stays, unless the new one fixes something an S60 needs (an ESP32-C3
with no secure boot or flash encryption); the bootloader of ESP-IDF 5.x starts
the apps of later 5.x releases. Replacing it is a decision of its own:
`--new-bootloader`, then the whole fault suite and real plugs.

## By itself

Every Monday `.github/workflows/firmware-update.yml` asks which release of each
project - Tasmota, ESPHome and OpenBeken - has **settled** (`python3 tools/repo/update_firmware.py due`). A release has
settled when it is

1. a stable release - never a beta, a pre-release or a development build;
2. the newest of its series (Tasmota's `16.0.x`, ESPHome's month, `2026.10.x`;
   OpenBeken has no point releases, so each of its releases is a series):
   no later point release; and
3. at least 7 days old, and either a newer series has begun or it is at least
   14 days old (`SETTLE_MIN_DAYS`, `SETTLE_DAYS`).

A fix for a bad release has come within days or not at all - Tasmota 14.4.0
was followed by 14.4.1 in four days, 15.0.0 by 15.0.1 in one, each `.0`
withdrawn - so this lets it come first. For Tasmota, a release every two months
or so, it means 14 quiet days; for ESPHome, a series a month, the month
before's last point release once the new month's `.0` is out: always a month
behind, on a series that has had its fixes. A release listed under `hold` in
`firmware.json` (`"hold": {"tasmota": ["16.1.0"]}`) is never taken.

The workflow makes what is due into a pull request: the update (steps 1 and 2
below, its docs and changelog line written, the version raised a minor version),
for every device, a device added included - then the unit tests, and on the pull
request the emulator check of every device (step 4), which main requires. Nothing is
pushed if the leak scan finds anything in it. The pull request says which
releases, what each check found, and the tests' results; the workflow then
starts the lint, leak-scan and emulator-check workflows on its branch, as a
pull request opened with a workflow's own token starts none itself, so the
checks `main` requires are there. One whose tests passed **merges itself 7
days after it was opened, which is the release**, unless it is labelled
`hold`; one whose tests failed, or a check `main` requires, is labelled
`tests-failed` and never merged by the workflow. A pull request is merged only
if `main` has not moved on since it was made and tested; otherwise it is
closed, and the next week's run makes it again.

The same run checks the pinned Tasmota release is still published - each
conversion downloads its files - and opens an issue if it has gone, taking its
series' newest release at once where there is one. It also opens one once
nothing has reached `main` for 50 days: GitHub switches a public repository's
schedules off after 60 days with no activity, this one with them.

By hand, from the Actions tab: `tasmota` or `esphome` takes a given release
whether settled or not, and `dry_run` makes and tests the update and opens
nothing. `python3 tools/repo/update_firmware.py check` says what is pinned and what
is latest.

## By hand

Work on a branch from `main`. Each step below is one commit.

### 1. Tasmota

```sh
python3 tools/repo/update_firmware.py tasmota            # the latest release
python3 tools/repo/update_firmware.py tasmota 16.0.0     # or that one
```

It reads the release from GitHub (refusing a draft or pre-release) and its
tag's commit, downloads its three ESP32-C3 files, and checks:

- the factory image's partition table is, byte for byte, the one the
  conversion writes (`TARGET_TABLE_SHA256`), and the Safeboot and the app in
  it are the release's own files, at the offsets the conversion writes them
  to;
- the Safeboot fits its partition and the app fits app0, each a valid ESP32-C3
  image;
- `CFG_HOLDER` in the release's `my_user_config.h` is the one the recovery
  Safeboot's settings record has (`tools/autoflash/tasmota_settings.py`).

Then it writes the release, its commit and each file's size and SHA-256 into
`firmware.json`. `--dry-run` checks without writing.

Where it stops:

- **Another partition table**: Tasmota has changed its layout. The repartition
  (`TARGET_PARTITIONS`, the Berry phases in `tools/espressif/safeboot/repartition/berry/`) was reviewed
  for this one; a new layout is a new design, not an update.
- **Another `CFG_HOLDER`**: the release would start without the Wi-Fi the
  recovery Safeboot saved, and every conversion would end on its setup
  network. The recovery Safeboot would have to be rebuilt from that release
  (`artifacts/README.md`).
- **A file too large**: the Safeboot or app no longer fits Tasmota's own
  layout - which Tasmota would have changed first.

### 2. ESPHome

```sh
python3 tools/repo/update_firmware.py esphome            # the latest release
python3 tools/repo/update_firmware.py esphome 2026.10.0
```

A change to `devices/sonoff-s60/esphome.yaml` is published the same way, naming the
release already pinned, so that only the config changes.

It needs Docker: the image is built in ESPHome's own image for that release
(`ghcr.io/esphome/esphome:<release>`), pinned by its digest, so the build does
not depend on what is installed on the computer building it. It checks, then
builds:

- the parts of ESPHome that `devices/sonoff-s60/esphome.yaml` reaches into beyond its
  options - the fixed key its last `on_boot` lambda saves the Wi-Fi under, and
  the layout of what it saves ([devices/sonoff-s60/esphome.md](../devices/sonoff-s60/esphome.md) says
  why) - are as they were; if not, the lambda must be checked first;
- the config's `project` version is raised (`2026.10.0-2` is this project's
  second build on ESPHome 2026.10.0, the number starting again at 1 on a new
  release), its `package_import_url` is set to the release it will
  come out in, the top of `CHANGELOG.md` or the next after it, and it is built.
  A plug adopted into Home Assistant keeps to that release until its owner
  names a newer one, and the release workflow refuses a version whose configs
  name a release that is not out: rebuild them if the version changes first;
- the app is an ESP32-C3 app that fits Sonoff's slot, says it is that ESPHome
  release, holds each Wi-Fi placeholder once and untouched and takes a Wi-Fi
  written in, and holds no path from the computer that built it;
- its bootloader is compared with the published one. If they match, nothing
  changes. If they differ (a new ESP-IDF), the published one is kept, and the
  command says so - see above.

Then it publishes the image in `artifacts/` (removing the one before), and
updates its line in `SHA256SUMS`, the config's version and `firmware.json`.
`--mover` builds and publishes the mover the same way, only for a change to
its config. `--dry-run` builds and checks, and publishes nothing. ESP-IDF and
its tools are downloaded once, to `captures/esphome-build/`.

An ESPHome build is not byte for byte repeatable - it holds the time it was
built and where - so what pins a build is the Docker image it was made in, in
`firmware.json`. The bootloader is repeatable: built again with the release
that made the published one, it comes out the same.

### 3. OpenBeken and ESPHome for the S61

A Beken plug ends on OpenBeken or on ESPHome built on LibreTiny, each an
rt_ota image the plug's stock bootloader applies from its download slot
([devices/sonoff-s61/](../devices/sonoff-s61/README.md)); the images are
pinned under `beken` in `firmware.json`, by plug.

A change to what the OpenBeken driver does on first boot reaches only plugs
converted after it: an updated plug keeps its settings, so the import does not
run again. A fix that updated plugs need goes in what the driver does on every
start ([openbeken.md](../devices/sonoff-s61/openbeken.md#updating)).

```sh
python3 tools/repo/update_firmware.py openbeken                       # again on what is pinned
python3 tools/repo/update_firmware.py openbeken --release 1.18.321    # moved to a stable release
python3 tools/repo/update_firmware.py esphome 2026.10.0 --plug sonoff-s61
```

`openbeken` clones [OpenBK7231T_App](https://github.com/openshwprojects/OpenBK7231T_App)
at the commit pinned (or `--release`; with none pinned, its latest release) and its
SDK, applies this project's patches
([`tools/beken/openbeken/`](../tools/beken/openbeken/): the 4 MB table,
offered upstream, and the hook before an OTA's restart the driver uses),
adds this project's OpenBeken driver
(`tools/beken/openbeken/drv_autoflash.c` and `drv_autoflash.h`) and the
plug's table for it (`devices/sonoff-s61/drv_autoflash_sonoff_s61.c`) to
the build and to start-up as [openbeken.md](../devices/sonoff-s61/openbeken.md)
says, and builds `OpenBK7238` with `FLASH_SIZE=4M` in this project's build
image (`tools/beken/openbeken/build.Dockerfile`, Ubuntu 24.04: the SDK's own
toolchain wants a newer GLIBC than OpenBeken's image has). It checks the
image is an rt_ota app of the version asked, fits the download slot, and
holds each command the driver registers; then it publishes it, recording the
OpenBeken and SDK commits, the OpenBeken release the commit is (or the latest
before it, which the README's badge shows), the hashes of the driver, the table
and the patches, and the build image, and writes its row, `NOTICES.md` and the
changelog. It moves only to a stable release (`--release`), as the weekly update does
once one has settled, for every Beken device at once (`--every-device`). The
version comes from what it builds: `1.18.320-2` is this project's second build on OpenBeken's release
1.18.320, the number starting again at 1 on a new release.

`esphome --plug sonoff-s61` builds `devices/sonoff-s61/esphome.yaml` in
ESPHome's image for the release, as the S60's is built, and takes
`image_bk7238_app.ota.rbl`: checked as an rt_ota app that fits the slot, says
its version, project and ESPHome release, and has the Wi-Fi action in it,
with the build's download partition where the stock bootloader looks. Its
first boot reads the plug's Wi-Fi and calibration from the stock store, so
the image holds no placeholders and nothing is written into it. The config's
version is raised and the image published as the S60's is, with its docs.

### 4. The docs

Each update writes what it changed, and `tests/repo/test_firmware_pins.py` fails
while any of it is missing:

- `artifacts/README.md`: the image's row (its name and SHA-256), the ESPHome
  release that builds it, and a paragraph on the new build;
- `NOTICES.md`: the image's name, ESPHome release and ESP-IDF
  version;
- `devices/sonoff-s60/esphome.md`: the ESPHome release that built the current image;
- for the S61's ESPHome, the same, with its LibreTiny version, in
  `devices/sonoff-s61/esphome.md`;
- for its OpenBeken, by hand: its row in `artifacts/README.md` and its paragraph
  in `NOTICES.md` (the OpenBeken and SDK commits);
- `CHANGELOG.md`: a line for each firmware, with a link to its release notes,
  under the release to come - a new section, a minor version on, once the one
  above has been released, with `tools/autoflash/__init__.py` raised to it.

For a change to the config, built with the release already pinned, they say
the config changed, and what the change does is left to write by hand in
`CHANGELOG.md` and `artifacts/README.md`.

Each passage is found and rewritten, and if one is not found nothing is
changed: `--no-docs` leaves them all to be written by hand, as a change to
the mover or the bootloader (`--mover`, `--new-bootloader`) needs anyway. The
README's badges read `firmware.json` on `main`, and change by themselves.

### 5. Test

**Unit tests**, in a checkout with no emulator state linked in:

```sh
python3 tests/run.py
```

**The emulator**, from just before the final firmware: nothing before it
changes, and it was tested by the fault suite when it last did. Two public
plugs ([emulator.md](emulator.md#public-plugs)) - one after E3 on the way to
Tasmota, one after D5 on the way to ESPHome - are taken to their final
firmware (F2 to H1, and I1 and I2), and the plugs that makes are then taken
each to the other (L1 to I2, and X1 to H1): every step that writes, starts,
checks or runs code on the final firmware, in four or five minutes on two
emulators - one for each way, each going on to its second run as soon as its
first is done. The plugs hold nothing of Sonoff's, so no cloud is needed, real or
stub, nor an account:

```sh
python -I tools/espressif/emulator/public_plugs.py test
```

All four must convert. The repository's `emulator-check` workflow runs the
same for each device a change could affect (`tools/repo/emulator_check.py`, which
has every device the tool converts), with the unit tests, on GitHub's own runners, from the published public
plugs and emulator image, whenever the tool or its firmware changes. The faults are not put in their way: they test the
tool's own recovery, which a new final firmware does not change. Where the
recovery leans on the firmware's own behaviour, a major release (16 to 17,
say) or release notes touching its Wi-Fi, settings, updates or Berry, run
those few scenarios of the fault suite too - F4's reset, G1, `seven-times`,
and the network gone at L1 and X1 ([testing.md](testing.md#the-fault-suite)).

**The S61's OpenBeken**, built anew - above all on a new OpenBeken release -
is run on the S61 in the Beken emulator before it is published: the weekly update runs these
six scenarios itself, and a pull request whose scenarios failed never merges itself. This project's driver
and its hook before an OTA's restart lean on OpenBeken's own workings: where
it keeps its settings and its Wi-Fi's (`0x3e1000`, and its flash variables
at `0x1e5000`), how its HTTP OTA ends in a restart. A commit can move either
with the build still passing and the image still holding each command, and
the interim firmware would then leave its settings behind for ESPHome or a
later OpenBeken to find. The starting plugs are made again with the new
image, then six scenarios run, each doing once something the driver or the
hook does - the Wi-Fi taken from Sonoff's firmware, the interim firmware
named, fetching ESPHome and erasing itself, kept as OpenBeken, each way back
to Sonoff's firmware - where a pass also checks the names the plug asked
DHCP for and, after ESPHome, that the interim's settings are gone
([testing.md](testing.md#the-beken-fault-suite)):

```sh
tools/beken/emulator/fault_suite.py prep
tools/beken/emulator/fault_suite.py list openbeken > openbeken.txt
rm -rf captures/emulation/beken/suite-openbeken   # no pass on the image before counts
AUTOFLASH_BEKEN_EMU_SUITE_DIR=captures/emulation/beken/suite-openbeken AUTOFLASH_BEKEN_EMU_WORKERS=2 \
    tools/beken/emulator/fault_suite.py batch openbeken.txt
```

All six must pass.

**A real plug**, converted from Sonoff to each firmware and between them,
before the release: an emulator does not show real flash, radios or timings
([testing.md](testing.md#real-plugs)).

### 6. Release

The update raised the version in `tools/autoflash/__init__.py` to the
changelog's; merging to `main` publishes the release
(`.github/workflows/release.yml`), once the tests, lint and leak scan pass on
`main` again, with the firmware files attached. Runs left
part way by the release before carry on with the new firmware by themselves:
the bundle is built again for the new pins - at A5, or as a run carried on
past A5 starts - and a step whose checks were made with the old bundle does
them again. A run that had already installed the official app (F2) finishes on the
release it began with, using its bundle - or, if this version must build that
again (1.2.0's, from another startup firmware), a bundle of the same release.
It reads the app back against that release's files as usual, and says so: `firmware.json` keeps each
release pinned before (`earlier`) by its files, and `update_firmware.py
tasmota` adds the one it replaces.
