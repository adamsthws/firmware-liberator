# Espressif research

What is known about the ESP32 and ESP32-C3 devices the tool converts or might, and how
it was found out, so you need not repeat the work. [device-research.md](device-research.md)
lists the rest, by chip family and by device; [Beken research](beken-research.md) is
laid out the same way.

"Emulator" below means the plug ran under QEMU against the stub cloud
([emulator.md](emulator.md)). Nothing here has been run on a real plug unless it says
so. The emulator has no relays, meters, buttons or screens, so it cannot prove a
device's wiring. The legal footing for studying a plug's firmware, the same for every
device and both chip families, is in [legal.md](legal.md#studying-and-emulating-the-firmware).

## Devices

| Device | Chip | Layout | Firmware name | Source of the firmware | In the emulator |
|---|---|---|---|---|---|
| S60TPG | ESP32-C3 | S60 style | `FWSW-E32S60-S60-ESP32C3FN4` | whole dump | converts both ways |
| POW Elite (POWR316D) | ESP32 | S60 style | `FWSW-E32POWP-POWPRO-ESP32D0WDV3` | whole dump | converts both ways, and back |
| POWCT | ESP32 | S60 style | `FWSW-E32POWCT-POWCT-ESP32D0WDV3` | whole dump | converts both ways |
| TH Elite (THR320D) | ESP32 | TH style | `FWSW-E32THR3-THR3-ESP32D0WDV3` | whole dump | converts both ways |
| Dual R3 | ESP32 | TH style | `FWSWES-E32DR3-DUALR3-ESP32D0WDV3` | update file, on the TH Elite's flash | converts both ways |
| NSPanel | ESP32 + PSRAM | S60 style, no `reserve` | `FWPNL-E32NSP-NSPANEL-ESP32D0WDV3` | whole dump | boots; rejects a one-record file at the header ([why](../devices/not-ready-yet/sonoff-nspanel/research.md#the-nspanel-wants-three-records)) |
| BASICR4 | ESP32-C3 | unknown | `FWSW-E32R4-BASICR4-ESP32C3` | update file | does not start on the donor (`espInit`, `ESP_ERR_NOT_FOUND`); added from the official file, testers wanted |
| MINIR4 | ESP32 + PSRAM | S60 style | `FWSW-E32R4-MINIR4-ESP32D0WDR2V3` | update file | goes to `factory mode`: wants a real unit's factory data |
| M5 1C, 2C, 3C (not Matter) | ESP32 | unknown | `FWSWES-E32T5-T5-ESP32D0WDV3` | update file | goes quiet, never starts its Wi-Fi |

Stock models, as a firmware calls itself: `E32-MSW-NX` (NSPanel), `E32-2SW-P0` (Dual
R3), `ESP32D0-MSW-T5` (M5). The POW Elite shares its stock model with the POW Elite
20A (POWR320D), which is wired differently; the same goes for the TH Elite and
THR316D. That is why a kind can say its eWeLink product decides.

### Locked devices

Reported locked, with secure boot and flash encryption:
[MINIR4M](https://www.elektroda.com/news/news4160452.html), M5 Matter versions
([M5-3C-80W](https://www.elektroda.com/rtvforum/topic4125389.html); the public dump of
it is encrypted, so unusable) and MINI-D.

Matter is not itself the lock. The S61 carries a full Matter stack and is not locked:
its updater checks integrity only, with no signature. What marks the locked ones is
Espressif's secure boot on an ESP32 Matter build. To tell before writing anything:

- the partition labels `esp_secure_cert`, `nvs_matter` or `fctry`;
- `secure boot v2` or `flash encryption is enabled` in a serial boot log;
- `Download mode is disabled` with GPIO0 held low;
- a dump with no readable partition table.

Each backup logs the fuses (below), so a run says so too.

## Flash and layouts

| Partition | S60 style | TH style | Locked Matter |
|---|---|---|---|
| `esp_secure_cert` | | | `0xf000`, `0x2000` |
| `nvs_matter`, `nvs_matter_bkp` | | | `0x11000`, `0x17000` |
| `nvs` | `0x9000`, `0x10000` | `0x9000`, `0x40000` | `0x1d000`, `0xf000` |
| `reserve` | `0x19000`, `0x4000` | | |
| `otadata` | `0x1d000`, `0x2000` | `0x49000`, `0x2000` | `0x2c000`, `0x2000` |
| `phy_init` | `0x1f000`, `0x1000` | `0x4b000`, `0x1000` | `0x2e000`, `0x1000` |
| `ota_0` | `0x20000`, `0x1f0000` | `0x60000`, `0x1a9000` | `0x30000`, `0x1e0000` |
| `ota_1` | `0x210000`, `0x1f0000` | `0x210000`, `0x1a9000` | `0x210000`, `0x1e0000` |
| `fctry` | | | `0x3f0000`, `0x6000` |

`ota_1` is at `0x210000` in all three, and all are 4 MB. These are a pattern to guess
from, not something to assume: the Dual R3 was first guessed as S60 style, and a
real one's boot log showed TH style. A real plug's table is compared with the one
its kind expects, by hash, after its backup, and a run with another stops before
anything of Sonoff's is written over.

Sources for those known only from a real unit:
[Dual R3 boot log](https://community.home-assistant.io/t/help-with-flashing-sonoff-dual-r3-please/297547?page=2)
(looks stock, by its labels and IDF 4.1; the poster does not say it was never
flashed),
[MINIR4 layout](https://github.com/smeso/esp32-ptm216b),
[MINI-D boot log](https://community.home-assistant.io/t/can-you-flash-a-sonoff-mini-d/932312).
No stock table was found for the BASICR4, non-Matter M5 or Dual R3 Lite.

### Leaving Tasmota on a TH-style layout

There is no room for the 715 KB mover between `ota_0` at `0x60000` and Tasmota's app at
`0xE0000`: 512 KB. It is not smaller flash: the boot log shows 4 MB, with a 256 KB `nvs`
pushing `ota_0` up. So these plugs leave through a temporary table: `nvs` `0x9000`,
`0x14000`; `otadata` `0x1D000`; `phy` `0x1F000`; `ota_0` `0x20000`, `0x1F0000`; `ota_1`
`0x210000`, `0x1A9000`. The mover goes at `0x20000`, in Tasmota's Safeboot area, the
interim Tasmota in `ota_1`, and the Tasmota of the hub then writes the real table last.

In the emulator, TH Elite and Dual R3 go from Tasmota to Sonoff and to ESPHome, and the
TH Elite survived power cuts at each stage of its way to Sonoff. A cut inside the table
plan was built by hand, not timed, and a cut mid-write while leaving was not reached.

### One bootloader

Every plug ends with ESPHome's bootloader, however it got Tasmota. Leaving Tasmota never
writes it: stage D5 does, from the interim Tasmota. A plug going from Tasmota to ESPHome
takes the direct route only if it has that bootloader already, read by its hash, and
otherwise goes through the interim Tasmota. Tasmota's own bootloader starts the mover:
seen in a POW Elite's boot log, in the emulator.

Leaving Tasmota, Berry has about 28 KB free on the full build. A whole write held a
classic ESP32's 26 KB bootloader twice, and stopped there. It now compares the hash on
the plug first, as every conversion has already put that bootloader in place.

## A plug's identity and stores

What makes a plug the device its eWeLink account knows is a few entries of Sonoff's
NVS, 300 to 550 bytes: `frame_params/content` and `data_backups/content` on every
Espressif plug, `productId/productId` on the S60, `channel_params/content` and
`PID/content` on the POWCT. The POW Elite also caches eWeLink's server
certificates (`wss_ca1` to `wss_ca4`, about 5.9 KB), which are not identity. Neither
is the Wi-Fi (`nvs.net80211`, `frame_params`' `ssid` and `passwd`), the histories,
logs or radio calibration.

On a real S60, against its account record: `frame_params.deviceid` is the record's
`deviceid`; `frame_params.apikey`, `encrypt_key` (where there is one) and
`data_backups.apikey` are its `devicekey`; `model` and `uiid` are `extra.model` and
`extra.uiid`. Not in the record: `sha256` (64 hex digits), `init`, the dispatch
server and port, and the Wi-Fi. So `sonoff_identity.from_account` rebuilds
`frame_params` and `data_backups` from the record, with the account region's
dispatch server (`eu-dispd.coolkit.cc`), `encrypt_key` and `is_encrypt` on a
classic ESP32 plug as their dumps have them, and no `sha256`.

In the emulator, against the stub, the S60's firmware 1.2.0 registers and reports
its state the same with `sha256` as it was, without it, and as 64 zeros; and with
`frame_params` cut to `init`, `uiid`, `deviceid`, `apikey` and `model`, no
`productId`, `data_backups` or user settings. `sha256` is sent neither in dispatch
nor in register, so nothing there checks it. Whether the real eWeLink accepts a
plug rebuilt this way has not been tried.

The ESPHome route keeps Sonoff's layout and NVS, identity included; the Tasmota
route shrinks the NVS to 20 KB, which loses it. So the tool keeps it in a sector of
its own at `0x3FF000` ([technical-details.md](technical-details.md#an-espressif-plug-on-tasmota)).
What was seen of it in the emulator:

| What | S60 | POW Elite |
|---|---|---|
| Stock to Tasmota: the sector holds the identity, the table its entry, Tasmota boots, its filesystem (316 KB) takes a file | yes | yes |
| A Tasmota OTA update through its safeboot (`Upgrade 1`) | kept | |
| `Reset 1`, `Reset 2`, `Reset 5`, `Reset 6` | kept | |
| The filesystem formatted (its first two blocks erased; Tasmota made it again) | kept | |
| The filesystem filled up | kept | |
| Tasmota's factory image written over it from `0x0` | kept; the table's entry gone, found by its magic | |
| Partition Wizard (v16.0.0, loaded by hand) | offers "Resize FS to max" by 4 KB; doing it drops the entry, and the filesystem filled up wrote over the sector | |
| Back to Sonoff, no backup, from the kept identity: Sonoff's firmware online on the stub with its own id and key | yes, after the factory image too | yes |
| Back to Sonoff, no backup, identity from the account record alone | yes | yes |
| Tasmota to ESPHome: the identity in ESPHome's settings, and the sector kept | yes | |
| Stock to ESPHome: Sonoff's settings kept, and the sector written too | yes | |
| From either ESPHome back to Sonoff, no backup: online with its own id and key | yes, from the sector | |
| TH Elite stock to Tasmota (sector kept; at `0x3FF000` it is in no partition of its own layout) | yes | |

The Partition Wizard looks for unallocated flash past the last *filesystem*
partition, not past the last partition, so the `sonoff` area counts as free; and its
"Resize FS to max" begins by removing every non-app partition after the filesystem
(`remove_partition_after_last_fs`, in `resize_max_flash_size_k`), whatever the flash
size. Its other resize, which needs the filesystem last, is not offered.

Just in front of the filesystem, at `0x3AF000` (the last 4 KB of `app0`), was tried
as well, and is not used. On Tasmota's layout it is the better place: the filesystem
keeps its 320 KB, the Wizard's only offer there ("Resize FS") refuses every size
("last `app` partition has no firmware"), and after a factory image it is reached only
by an update of 0x2D0000 bytes, as Tasmota's page upload stops 4 KB short of `app0`'s
end. But every plug passes through Sonoff's layout, where `0x3AF000` is inside the
interim Tasmota the hub runs from `ota_1` on an ESP32-C3 (`tasmota32c3-bluetooth.bin`,
ending at `0x3C92C0`; a classic ESP32's ends at `0x377230`). On the S60, writing it
there damaged the running image, and every way back writes that image over it again,
so going back fell back on the eWeLink account. Updates erase only as far as their
image: Arduino's `Update` by the size it began with, ESP-IDF's `esp_ota_begin` the whole
partition only for size 0 or unknown, and ESPHome 2026.10.0 to its next 64 KB past the
image.

The startup firmware wipes NVS when it finds no Tasmota settings there
(`persist-settings.patch`), as on the way from the mover or from ESPHome to the
interim Tasmota. So the identity L1 writes into the settings, or ESPHome kept, is
gone by the time R1 runs; the sector at the end of `ota_1` is not, as every update on
the way writes only as far as its image. R1 reads it there, and the tool keeps it
on the way to ESPHome too.

## The update path

A stock plug takes its conversion as an update: an app inside Sonoff's 100-byte
wrapper, which its updater refuses unless the wrapper names its own firmware
([below](#learning-the-firmware-name)).

The emulator cannot run Sonoff's official update file as eWeLink serves it: every
Sonoff app it runs has Bluetooth start-up skipped and the stub's CA swapped in. So
the stub offers the plug's own app made so (`stub_cloud/firmware.py`), as eWeLink's
latest, to `ota/query` with an older version, and serves it to the device's id;
fetching, unwrapping and checking it is the same code as against eWeLink.

### Learning the firmware name

A stock updater refuses a file whose wrapper names another firmware. In the emulator
the POW Elite read 100 bytes of a wrong one, reported `ota: fail` and stayed on Sonoff,
so a wrong name costs nothing. The names vary too much to guess.

Tried on the real server:

| Asked | Answer | Tried on |
|---|---|---|
| `ota/query` with the device's current version | no offer | S60 |
| `ota/query` with its own id and version `0.0.1` | its latest firmware | S60, S61 |
| its own id, with another model | its own firmware: the model is ignored | S60 |
| a made-up id, with any model | an unrelated 3.5.0: never use one | |
| an empty id | error 400 | |
| GET on the offered URL as given | empty 400 | S60 |
| GET with `?deviceid=<its id>&ts=1&sign=0` | the file; the signature is not checked, and a `Range` is answered | S60, S61 |

`tools/ewelink/firmware_name.py` uses this at stage 5, reading only the file's first
bytes. Where eWeLink offers nothing, it falls back on the names in the public update
files' filenames ([FlashDumps](https://github.com/openshwprojects/FlashDumps)
`IoT/zOTAz`), giving a name only when exactly one fits. That finds all 13 kinds and
none wrongly. A file for a run of gangs, such as the M5's `M5-1C-3C`, is each gang's
in it (`M5-2C-86`), unless a file names the device itself, as `M5-3C-80W`'s does.

A wrapper's first byte pair is `03 01`, its record count is byte 1, and 76-byte records
start at `0x18`. An NSPanel file has 3 names and an SPM 2. A Beken file has the same
wrapper after an outer header of 16 bytes plus the 16-bit value at byte 12.

## What a backup logs

`tools/espressif/device_facts.py` puts in the run's log the chip and revision, secure
boot, flash encryption, flash size, the partition table, and each app's version,
ESP-IDF and firmware name, on screen too when the table is not known. A classic
ESP32's eFuse file cannot tell revision 2 from 3, so from the file alone it logs "at
least".

At the same step the run asks the installer's `GET /info`, once, and logs the answer:
the exact chip revision in place of "at least", then `Installer says` lines with the
chip and its cores, whether its flash is embedded, the flash chip's JEDEC id and real
size, the running and fallback slots, PSRAM and input pin levels. An installer before
4.4.5, or one that does not answer, costs the run nothing: the log only lacks these lines.

Some of it has limits:

- **PSRAM.** The chip's fuses say whether PSRAM is in its package. PSRAM on a chip of
  its own is not looked for: that would drive pins a board without it may use for
  something else. So "no" in the package does not mean the plug has none. An ESP32-C3
  cannot have any.
- **Pin levels.** Levels are read from the chip's input registers, and nothing about a
  pin is changed. Pins whose input is off (`gpio input off`) read 0 whatever is on them,
  so they are named and not read. The flash chip's pins are left out: 6 to 11 on a classic
  ESP32, 12 to 17 on an ESP32-C3. A level is a moment's snapshot in the installer, with
  the board's own pull-ups and whatever stock left behind, not a pin map.

In the emulator the answer comes back whole, but QEMU has no pads or revision register:
every pin says its input is off, so `gpio levels` is empty, and a classic ESP32 says
revision 0.0. What a real chip answers has not been seen yet.

## Tools and methods

- **The emulator**: the stock firmware under QEMU against the stub cloud
  ([emulator.md](emulator.md)); a device with no whole dump runs its update file on a
  donor's flash, as the table above says.
- **Reading a plug and its firmware**, in [`tools/espressif/`](../tools/espressif/README.md):
  `flashmap.py` (what a dump holds: its chip, partition table and MAC), `layouts.py`
  (the tables Sonoff's firmware comes on), `sonoff_identity.py` (a plug's identity,
  and `from_account`), `device_facts.py` (what a backup logs), and
  `build_vendor_ota.py` / `analyze_vendor_ota.py` (the 100-byte wrapper a stock
  updater takes).
- **eWeLink**: `tools/ewelink/firmware_name.py` (the firmware name), and the stub's
  `stub_cloud/firmware.py` (the plug's own app offered as eWeLink's latest).

## Questions still open

- What the M5 waits for after `factory?`, and what BASICR4's `espInit` cannot find.
  Neither input pin levels nor eWeLink's model number for the M5 (160 to 162) moved it.
  Both run on a donor flash from an update file; the only whole M5 dump published is the
  encrypted Matter one (above), so neither has a plaintext dump to compare against yet.
