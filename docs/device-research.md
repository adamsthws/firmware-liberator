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
| NSPanel | ESP32 + PSRAM | S60 style, no `reserve` | `FWPNL-E32NSP-NSPANEL-ESP32D0WDV3` | whole dump | boots; turns the installer down after 24 bytes |
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
ESP32's eFuse file cannot tell revision 2 from 3, so it logs "at least".

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

## Why classic ESP32 plugs needed emulator work

Each of these is general, not per device:

| What you saw | Cause | Fix |
|---|---|---|
| Boot 26 s, Tasmota's settings backup about 70 s | QEMU threw away translated code and changed the memory map at every flash operation | `patches/0003` |
| Interrupt watchdog at start-up | a core spinning on a lock waited up to 100 ms for the other's turn | `patches/0004` |
| Interrupt watchdog in `esp_dport_access_stall_other_cpu_start` | the workaround for a silicon bug QEMU does not have: the other core can be slow to answer | `patch_stock.py` skips it on ESP-IDF 4 |
| `A stack overflow in task colink task` during TLS | the firmware's stack is sized for eWeLink's RSA-1024 CA, not an EC one | an RSA stub CA where the firmware's is RSA |
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

- Why the NSPanel turns the installer down at byte 24: both a wrapped and a raw file were
  refused there, so the wrapper's first 24 bytes are where to look.
- What the M5 waits for after `factory?`, and what BASICR4's `espInit` cannot find.
  Neither input pin levels nor eWeLink's model number for the M5 (160 to 162) moved it.
- Whether a real plug answers as the emulator does: `/api/flash` returning raw flash on a
  Beken plug, and stock's checks of a rebuilt image.
