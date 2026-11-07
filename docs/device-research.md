# Device research

What is known about Sonoff devices the tool converts or might, and how it was found
out. It saves you repeating the work. To add a device, see
[adding-a-device.md](adding-a-device.md); what holds particular devices up is in
[devices/not-ready-yet/](../devices/not-ready-yet/README.md).

"Emulator" below means the plug ran under QEMU against the stub cloud. Nothing here
has been run on a real plug unless it says so. The emulator has no relays, meters,
buttons or screens, so it cannot prove a device's wiring.

## Espressif devices

| Device | Chip | Layout | Firmware name | Source of the firmware | In the emulator |
|---|---|---|---|---|---|
| S60TPG | ESP32-C3 | S60 style | `FWSW-E32S60-S60-ESP32C3FN4` | whole dump | converts both ways |
| POW Elite (POWR316D) | ESP32 | S60 style | `FWSW-E32POWP-POWPRO-ESP32D0WDV3` | whole dump | converts both ways, and back |
| POWCT | ESP32 | S60 style | `FWSW-E32POWCT-POWCT-ESP32D0WDV3` | whole dump | converts both ways |
| TH Elite (THR320D) | ESP32 | TH style | `FWSW-E32THR3-THR3-ESP32D0WDV3` | whole dump | converts both ways |
| Dual R3 | ESP32 | TH style | `FWSWES-E32DR3-DUALR3-ESP32D0WDV3` | update file, on the TH Elite's flash | converts both ways |
| NSPanel | ESP32 + PSRAM | S60 style, no `reserve` | `FWPNL-E32NSP-NSPANEL-ESP32D0WDV3` | whole dump | boots; rejects a one-record file at the header ([why](#the-nspanel-wants-three-records)) |
| BASICR4 | ESP32-C3 | unknown | `FWSW-E32R4-BASICR4-ESP32C3` | update file | stops at start-up: `espInit`, `ESP_ERR_NOT_FOUND` |
| MINIR4 | ESP32 + PSRAM | S60 style | `FWSW-E32R4-MINIR4-ESP32D0WDR2V3` | update file | goes to `factory mode`: wants a real unit's factory data |
| M5 1C, 2C, 3C (not Matter) | ESP32 | unknown | `FWSWES-E32T5-T5-ESP32D0WDV3` | update file | goes quiet, never starts its Wi-Fi |

Stock models, as a firmware calls itself: `E32-MSW-NX` (NSPanel), `E32-2SW-P0` (Dual
R3), `ESP32D0-MSW-T5` (M5). The POW Elite shares its stock model with the POW Elite
20A (POWR320D), which is wired differently; the same goes for the TH Elite and
THR316D. That is why a kind can say its eWeLink product decides.

## Layouts

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

## A plug's Sonoff identity

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
its own at `0x3FF000` ([technical-details.md](technical-details.md#the-finished-memory-layout)).
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

The emulator cannot run Sonoff's official update file as eWeLink serves it: every
Sonoff app it runs has Bluetooth start-up skipped and the stub's CA swapped in. So
the stub offers the plug's own app made so (`stub_cloud/firmware.py`), as eWeLink's
latest, to `ota/query` with an older version, and serves it to the device's id;
fetching, unwrapping and checking it is the same code as against eWeLink.

## Locked devices

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

## Learning the firmware name

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
`IoT/zOTAz`), giving a name only when exactly one fits. That finds 9 of 12 kinds and
none wrongly: the M5s' product names do not match their file's `M5-1C-3C`.

A wrapper's first byte pair is `03 01`, its record count is byte 1, and 76-byte records
start at `0x18`. An NSPanel file has 3 names and an SPM 2. A Beken file has the same
wrapper after an outer header of 16 bytes plus the 16-bit value at byte 12.

## The NSPanel wants three records

The NSPanel boots from its whole dump and then turns the bridge's file down the moment
it has read the 24-byte frame header, before any payload. The cause is in its stock
updater, read from the dump's app (the parser lives around `0x4014ad04`; the command it
answers is built around `0x400dca90`).

A 24-byte header carries, in order: a format byte (`03`), a one-byte record count, an
8-byte target version, reserved bytes, and at offset `0x14` a big-endian CRC-32 over the
first 20 bytes. The updater checks three things against the header, in this order, and
answers `ota: fail` (its error 6) on the first that does not hold:

1. **The CRC-32.** The bridge computes it the same way, so this passes.
2. **The version.** The header's 8-byte version must sort above the running firmware's.
   The bridge sends `9.9.9`, so this passes.
3. **The record count.** It must equal a number the firmware builds for itself, and for
   the NSPanel that number is the constant **3** (`movi.n a3, 3` at `0x400dcd00`) — not a
   value the cloud's upgrade message carries, so the stub cloud cannot change it. The
   three records are the ESP32 app and two HMI screen images, matching the public update
   file's `ESP32D0WDV3 + HMIX120 + HMIX86` names. The bridge sends one record, so the
   count is 1, it does not equal 3, and the file is rejected here. A raw image, with no
   header at all, is rejected sooner still.

So the NSPanel is not a CRC or version problem and not something a cleverer wrapper byte
fixes. To pass the gate the file has to present a three-record manifest: record 0 the
ESP32 app (whose 32-byte model name the updater memcmp's against
`FWPNL-E32NSP-NSPANEL-ESP32D0WDV3`, which the bridge already sends), and records 1 and 2
named `HMIX120` and `HMIX86`.

A three-record file cannot be made screen-safe in software, though, which is the real
blocker. The per-record checks and routing (parser state 2 at `0x4014ae6c`, writer at
`0x4014ac24`) were traced to confirm it:

- All three 76-byte records are validated up front: a CRC-32 over each record's first 60
  metadata bytes, record 0's model name, and a version field. The per-record CRC covers
  the **metadata only, not the payload**, so a dummy record's metadata passes even with a
  declared payload length of 0. The manifest check is not what stops this.
- What stops it is the routing. The handler for records 1 and 2 sends `whmi-wri
  ...,921600,0` to the display MCU over UART **at the start of the record, before any HMI
  payload is examined** — a zero-length or garbage HMI record still commands the physical
  screen into Nextion firmware-upload mode. The emulator has no display MCU, so this
  cannot be de-risked there; on a real unit it is the brick-the-screen risk.
- The ESP32 app (record 0) is only activated — boot partition set and reboot — after the
  **whole** three-record update completes. So you cannot both land the app and spare the
  screen: failing records 1 or 2 aborts everything (error 6, app never activated), and
  letting them succeed means really streaming firmware to the display.

So reaching Tasmota or ESPHome on the NSPanel needs either genuine `HMIX120`/`HMIX86`
images plus a real unit to prove and recover the display path, or a different entry point
altogether. One lead, not pursued and needing hardware to test: the stock handler builds
the manifest conditionally on two HMI-present flags it reads from runtime state (not from
the update file); if the firmware could be made to report no screen, the baked count
could drop below 3 and an ESP32-only file might match. The builder
(`tools/espressif/build_vendor_ota.py`) still emits a single record and gains nothing
from an NSPanel branch until one of these routes is proven, so none was added.

## Why classic ESP32 plugs needed emulator work

Each of these is general, not per device:

| What you saw | Cause | Fix |
|---|---|---|
| Boot 26 s, Tasmota's settings backup about 70 s | QEMU threw away translated code and changed the memory map at every flash operation | `patches/0003` and `0004` |
| Interrupt watchdog at start-up | flash operations, done with interrupts off, took that long (the cores taking turns more often made no difference) | `patches/0003` and `0004` |
| Interrupt watchdog in `esp_dport_access_stall_other_cpu_start` | the workaround for a silicon bug QEMU does not have: the other core can be slow to answer | `patch_stock.py` skips it on ESP-IDF 4 |
| `A stack overflow in task colink task` during TLS | the firmware's stack is sized for eWeLink's RSA-1024 CA, not an EC one | an RSA stub CA on a classic ESP32 whose firmware's is RSA |
| `abort()` straight after `Disabling RNG` | external RAM missing | `psram_mb` on the kind |
| `This chip is revision 0` on an ESP32-C3 | emulated eFuses too old | the revision the app's header asks for |
| QEMU killed part way through a download | its container held under 256 MB | 1 GB for a classic ESP32 |

Tried and dropped: QEMU's multi-threaded mode (no help, and slower) and stretching the
watchdogs (it hid real hangs for longer).

Leaving Tasmota, Berry has about 28 KB free on the full build. A whole write held a
classic ESP32's 26 KB bootloader twice, and stopped there. It now compares the hash on
the plug first, as every conversion has already put that bootloader in place.

## Beken devices

### One code path for every 4 MB eWeLink build

`tools/beken/stock_points.py` finds what the emulator needs in the firmware itself. On
every file below each point gave one answer, and each firmware reached ONLINE on the
stub. Apart from the S61's own dump, these ran on the S61's bootloader and settings, so
they show the firmware comes up, not how a real device's hardware behaves.

| Device | Firmware name | Version |
|---|---|---|
| S61 | `FWSW-01-S61-BK7238` | 1.0.2 (dump), 1.1.0 |
| Basic Gen5 (BASIC-1GS) | `FWSW-01-BASICR5-BK7238` | 1.0.4 |
| BASIC-1GSP | `FWSW-01-BASIC1GSP-BK7238` | 1.0.5 |
| S41 | `FWSW-01-S41-BK7238` | 1.0.6 |
| T6-3C | `FWSW-06-SWITCHES-BK7238` | 1.0.3 |

### Their flash

Each app holds the SDK's partition table as entries of owner, description, start,
length and options, so it can be read from an update file by its description strings.

| Partition | 4 MB (all of those above) | 2 MB (door sensor) |
|---|---|---|
| Bootloader | `0x0`, `0xf000` | the same |
| Application | `0x11000`, `0x250000` | `0x11000`, `0x121000` |
| OTA | `0x286000`, `0x15a000` | `0x132000`, `0xae000` |
| RF Firmware | `0x3e0000`, `0x1000` | `0x1e0000`, `0x1000` |
| NET info | `0x3e1000`, `0x1000` | `0x1e1000`, `0x1000` |

Further 4 MB entries, the same in all five files: NVS `0x3e2000`, `0xb000`; KEY
`0x3ed000`, `0x1000`; matter info backup `0x3ee000`, `0x8000`; matter info `0x3f6000`,
`0x8000`; matter factory info `0x3fe000`, `0x2000`. The OTA entry's description is
lowercase `ota`. Stock models in their strings: `SN-BK7238-BASICR5-01`,
`-BASIC1GSP-01`, `-S41-01`, `-T6C3-01`. A register message takes its model from factory
data, not those strings, and only the S61's has been seen.

The bootloader holds an RT-Thread FAL table that agrees. The update files' apps, at
1.35 to 1.41 MB, would not fit a 2 MB table's application partition either. That
these devices really have 4 MB is inferred from the files, not from a read-out.

**The largest open risk is a spec that disagrees.**
[CNX Software's review](https://www.cnx-software.com/2025/11/06/review-sonoff-basic-gen5-matter-switch-and-tx-gen2-matter-smart-wall-switch)
gives the Basic Gen5 "2 MB flash (SiP)", while its firmware holds only the 4 MB table
above, including an update slot at `0x286000`, beyond 2 MB. The firmware is the stronger
evidence, as it could not update itself on 2 MB, but nobody has read a real one's flash
here. Stock does not expose its flash size to a run: its register message gives
`romVersion`, `chipid` and `model`, and `[Flash]id:` appears only on serial.

What is known of the Basic Gen5's flash, from a search of every source found:

| Evidence | Says | Kind |
|---|---|---|
| [FCC internal photos](https://fccid.io/2APN5-BASIC1GS/Internal-Photos/Internal-photos-8589146), board `BASIC-1GS-WIFI V1.2 2025.06.06` | one chip, `BEKEN / BK7238 / AU5187XA`, and no separate flash chip: the flash is inside the package | direct, but neutral: the marking does not give the size |
| [Beken's datasheet V2.1](https://cionlabs.com/wp-content/uploads/2025/06/BK7238-Datasheet_V2.1.pdf) | "SiP Flash: 2 MB or 4 MB": QH322 (QFN32, 4 MB), QH321 (QFN32, 2 MB), QH202 (QFN20, 4 MB), QH201 (QFN20, 2 MB). 4 MB was added in V2.0, December 2024, before the board's date | indirect: both sizes existed |
| Its official firmware | only the 4 MB table, its update slot above 2 MB | indirect, strong |
| Sonoff's door sensor | a 2 MB table and a 2 MB dump: Sonoff matches the table to the chip | indirect |
| The S61s | flash ID `162085`, which [BK7231GUIFlashTool](https://github.com/openshwprojects/BK7231GUIFlashTool) maps to a 4 MB Puya P25Q32HB; its dump is the only 4 MB one among about 60 BK7238 dumps in FlashDumps | direct, but a sibling, with a QFN20 package |
| [CNX Software's review](https://www.cnx-software.com/2025/11/06/review-sonoff-basic-gen5-matter-switch-and-tx-gen2-matter-smart-wall-switch) | "2 MB flash (SiP)", word for word as for the Tx Gen2 and later the BASIC-1GSP; its photos do not show the chip | indirect: it reads like the older datasheet's text |
| Generic BK7238 pages, LibreTiny's board, forum posts | 2 MB | indirect: written before 4 MB existed, and the same was wrongly said of the S61s |

Probably 4 MB, then, but nobody has measured one. Sonoff's pages, the CSA listing, the
Elektroda thread and GitHub said nothing on it. One read-only test settles it: on a real
unit, BK7231GUIFlashTool in BK7238 mode, "Firmware backup (read) only", over TX1 and RX1;
its `Flash MID loaded:` line says `162085` for 4 MB, `152085` or `154285` for 2 MB. Stock's
serial boot log also prints `[Flash]id:`.

### Adding a device from a pin map

The Basic Gen5 is added this way; see
[its page](../devices/sonoff-basic-1gs/README.md) for what has been run. Pins from
[one post](https://www.elektroda.com/rtvforum/topic4167932.html) and OpenBeken's list.
BASIC-1GSP (no published pin map; it has a meter, and forum titles mention a pulse-driven
latching relay, which a wrong map could harm), S41 (no published pin map) and T6 (one
unit's pins only, with touch over a UART protocol OpenBeken has no driver for:
[saltech1's ESPHome work](https://github.com/saltech1/esphome-sonoff-t6-uart)) stay out.
Pins are never guessed.

Basic Gen5, S41 and T6 firmware lacks the `uiActive` string the S61 and BASIC-1GSP have, so
a check of a plug back on Sonoff re-sends `sledOnline` there instead, proven only on the
stub.

### Rebuilding a whole dump without a serial cable

The stock app is overwritten when OpenBeken goes on, but everything else can be read
through OpenBeken's `GET /api/flash`, and the computer can fetch the official file
itself. `tools/beken/rebuild_dump.py` combines them. Proven in the emulator on the S61:

| Region | Rebuilt from 1.1.0, against the stock bootloader's own apply of 1.1.0 |
|---|---|
| Bootloader, FAL, radio, factory data | the same |
| Application, RBL header, download slot | the same |
| EasyFlash stores | differ: they hold the plug's own later history |

It booted to ONLINE and converted to OpenBeken again. It cannot know the flash's unique
id, or the plug's exact old version if the file is newer, though about 680 KB of the old
app survives conversion and shows whether the versions match. A rebuilt dump holds the
plug's Wi-Fi password and keys, so it stays private. The bootloader fills a short last
block with `00 00 10 00 80 80 02 29` then zeros.

### 2 MB devices: on hold

The public door sensor is the one 2 MB device found, and it is a different firmware
family, not a smaller S61: colink rather than `ais_*`, no Matter, no `EF40` store, and
plain RBL updates with no FOTA container. Stock's `NET info` at `0x1e1000` holds data
there, including the MAC, and OpenBeken's 2 MB build writes its config over it, so a
conversion would destroy it unread. Supporting them needs a route of their own.

## Questions still open

- What the M5 waits for after `factory?`, and what BASICR4's `espInit` cannot find.
  Neither input pin levels nor eWeLink's model number for the M5 (160 to 162) moved it.
  Both run on a donor flash from an update file; the only whole M5 dump published is the
  encrypted Matter one (above), so neither has a plaintext dump to compare against yet.
  (The NSPanel's byte-24 rejection is now understood: [three records](#the-nspanel-wants-three-records).)
- Whether a real plug answers as the emulator does: `/api/flash` returning raw flash on a
  Beken plug, and stock's checks of a rebuilt image.
