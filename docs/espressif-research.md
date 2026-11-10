# Espressif research

What is known about the ESP32 and ESP32-C3 devices the tool converts or might, and how
it was found out, so you need not repeat the work. [device-research.md](device-research.md)
lists the rest, by chip family and by device; [Beken research](beken-research.md) is
laid out the same way.

"Emulator" below means the device ran under QEMU against the stub cloud
([emulator.md](emulator.md)). Nothing here has been run on a real device unless it says
so. The emulator has no relays, meters, buttons or screens, so it cannot prove a
device's wiring. The legal footing for studying a device's firmware, the same for every
device and both chip families, is in [legal.md](legal.md#studying-and-emulating-the-firmware).

## Devices

| Device | Chip | Layout | Firmware name | Source of the firmware | In the emulator |
|---|---|---|---|---|---|
| S60TPG | ESP32-C3 | S60 style | `FWSW-E32S60-S60-ESP32C3FN4` | whole dump | converts both ways |
| POW Elite (POWR316D) | ESP32 | S60 style | `FWSW-E32POWP-POWPRO-ESP32D0WDV3` | whole dump | converts both ways, and back |
| POWCT | ESP32 | S60 style | `FWSW-E32POWCT-POWCT-ESP32D0WDV3` | whole dump | converts both ways |
| TH Elite (THR320D) | ESP32 | TH style | `FWSW-E32THR3-THR3-ESP32D0WDV3` | whole dump | converts both ways |
| Dual R3 | ESP32 | TH style | `FWSWES-E32DR3-DUALR3-ESP32D0WDV3` | update file, on the TH Elite's flash | converts both ways |
| NSPanel | ESP32 + PSRAM | S60 style, no `reserve` | `FWPNL-E32NSP-NSPANEL-ESP32D0WDV3` | whole dump | boots; rejects a one-record file at the header ([why](../devices/sonoff-nspanel/research.md#the-nspanel-wants-three-records)) |
| BASICR4 | ESP32-C3 | table at 0xC000; the rest unknown | `FWSW-E32R4-BASICR4-ESP32C3` | update file, on the S60's flash with its table moved | starts and is read out ([below](#sonoffs-ais-sdk)); not converted: Tasmota and ESPHome look at 0x8000 |
| MINIR4 | ESP32 + PSRAM | S60 style | `FWSW-E32R4-MINIR4-ESP32D0WDR2V3` | update file | starts with the ais SDK's records and is read out ([below](#sonoffs-ais-sdk)); a conversion not tried yet |
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
real one's boot log showed TH style. A real device's table is compared with the one
its kind expects, by hash, after its backup, and a run with another stops before
anything of Sonoff's is written over.

Sources for those known only from a real unit:
[Dual R3 boot log](https://community.home-assistant.io/t/help-with-flashing-sonoff-dual-r3-please/297547?page=2)
(looks stock, by its labels and IDF 4.1; the poster does not say it was never
flashed),
[MINIR4 layout](https://github.com/smeso/esp32-ptm216b),
[MINI-D boot log](https://community.home-assistant.io/t/can-you-flash-a-sonoff-mini-d/932312).
No stock table was found for the BASICR4, non-Matter M5 or Dual R3 Lite.

### The Dual R3's work mode

The Dual R3 runs as two relays or drives a curtain motor. Its firmware (1.6.1)
keeps which as `deviceMode` in its user settings, the JSON in NVS namespace
`user_usual`, key `content`, and reports it to eWeLink as `workMode`: 1 relays
(it then loads each relay's saved state), 2 motor; 3 is a third mode, not looked
into. At start-up, finding no `deviceMode` or one outside 1 to 3, it writes 2 and
runs as a motor, refusing every switch from eWeLink with error 400. A `workMode`
update through eWeLink changes it. This was read
from the firmware's code (its start-up and its `workMode` handler) and seen in
the emulator; [SonoffLAN](https://github.com/AlexxIT/SonoffLAN) treats workMode 2
on a Dual R3 (uiid 126) as a cover. The firmware also puts all its user settings
back to their defaults, `deviceMode` 2 among them, when the account it registers
with is not the one its `user_unusual` settings name (`finalUserApikey`).

The emulator's Dual R3 runs on the TH Elite's settings, which have no
`deviceMode`, so [public_dump.py](../tools/espressif/emulator/public_dump.py)
gives it its own, `{"init":"true","deviceMode":1}`, and the firmware adds the rest
of its defaults. That a Dual R3 out of the box is in relay mode is assumed, not
seen: how its factory sets that is not known. The test copy's account is made
the stub's, so the firmware keeps the mode.

### Leaving Tasmota on a TH-style layout

There is no room for the 715 KB mover between `ota_0` at `0x60000` and Tasmota's app at
`0xE0000`: 512 KB. It is not smaller flash: the boot log shows 4 MB, with a 256 KB `nvs`
pushing `ota_0` up. So these devices leave through a temporary table: `nvs` `0x9000`,
`0x14000`; `otadata` `0x1D000`; `phy` `0x1F000`; `ota_0` `0x20000`, `0x1F0000`; `ota_1`
`0x210000`, `0x1A9000`. The mover goes at `0x20000`, in Tasmota's Safeboot area, the
interim Tasmota in `ota_1`, and the Tasmota of the hub then writes the real table last.

In the emulator, TH Elite and Dual R3 go from Tasmota to Sonoff and to ESPHome, and the
TH Elite survived power cuts at each stage of its way to Sonoff. A cut inside the table
plan was built by hand, not timed, and a cut mid-write while leaving was not reached.

### One bootloader

Every device ends with ESPHome's bootloader, however it got Tasmota. Leaving Tasmota never
writes it: stage D5 does, from the interim Tasmota. A device going from Tasmota to ESPHome
takes the direct route only if it has that bootloader already, read by its hash, and
otherwise goes through the interim Tasmota. Tasmota's own bootloader starts the mover:
seen in a POW Elite's boot log, in the emulator.

Leaving Tasmota, Berry has about 28 KB free on the full build. A whole write held a
classic ESP32's 26 KB bootloader twice, and stopped there. It now compares the hash on
the device first, as every conversion has already put that bootloader in place.

### Converting a table at 0xC000

The BASICR4's bootloader and app read its partition table at 0xC000. Where a
program looks is built into it (`CONFIG_PARTITION_TABLE_OFFSET`), not passed
on by the bootloader, and everything the tool installs is built for 0x8000:
the interim Tasmota, the mover, the safeboot, ESPHome and ESPHome's
bootloader, and the bridge, which reads 0xC000 only when 0x8000 is empty.
Tasmota's Partition Wizard (`partition_core.be`) writes the table at 0x8000 by
address, and the tool's plans write it there too (`plans.TABLE_ADDRESS`).
(Read in code and Tasmota's source; not yet tried.)

So Tasmota put in a slot would start under Sonoff's bootloader, look at 0x8000,
find no table and no NVS, and restart for ever: that is why bridge 4.4.6
refuses an update on such a device. Nothing upstream needs to change. The
likeliest way, inferred and not yet tried: the bridge writes a copy of the
0xC000 table's entries at 0x8000 first, and reads it back. Sonoff's bootloader
and app never read that sector, so a power cut there costs nothing. The
interim Tasmota then starts and sees the same partitions, and D5 puts
ESPHome's bootloader in place as on any device, after which the device is laid
out as any other converted one. Leaving Sonoff, the stale copy at 0xC000 goes
before the Tasmota layout's `nvs` (0x9000-0xDFFF) takes that range; going back,
the backup restores both. It needs a bridge built with dangerous flash writes
allowed (its sdkconfig aborts on them), a BASICR4 layout in `layouts.py`, and
a readout of a real one first: if its bootloader runs past 0x8000 - the
reason for 0xC000 may be a bootloader over 32 KB - a table there would
overwrite it, and the conversion must refuse. Custom Tasmota or ESPHome builds
for 0xC000 would also work, but would not be the official images, and the
Partition Wizard would write a table nothing reads.

## A device's identity and stores

What makes a device the device its eWeLink account knows is a few entries of Sonoff's
NVS, 300 to 550 bytes: `frame_params/content` and `data_backups/content` on every
Espressif device, `productId/productId` on the S60, `channel_params/content` and
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
classic ESP32 device as their dumps have them, and no `sha256`.

In the emulator, against the stub, the S60's firmware 1.2.0 registers and reports
its state the same with `sha256` as it was, without it, and as 64 zeros; and with
`frame_params` cut to `init`, `uiid`, `deviceid`, `apikey` and `model`, no
`productId`, `data_backups` or user settings. `sha256` is sent neither in dispatch
nor in register, so nothing there checks it. Whether the real eWeLink accepts a
device rebuilt this way has not been tried.

The ESPHome route keeps Sonoff's layout and NVS, identity included; the Tasmota
route shrinks the NVS to 20 KB, which loses it. So the tool keeps it in a sector of
its own at `0x3FF000` ([technical-details.md](technical-details.md#an-espressif-device-on-tasmota)).
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
| The filesystem grown over the sector (Wizard as in #25241, deletion confirmed): 200 rewrites of a 200-byte file | kept | |
| The same, an 8 KB file rewritten | written over at the 10th rewrite (about 80 KB); after another restart, kept for 12 | |
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
A fix is proposed upstream,
[arendst/Tasmota#25241](https://github.com/arendst/Tasmota/pull/25241): it lists a
partition after the filesystem, and removes it only once the user confirms
("Resize FS to max" and the migration's step 3 name it and ask); `coredump` is
still removed without asking.

Once the filesystem covers the sector, it is gone well before the filesystem is
full. LittleFS has no table of free blocks: after each mount it starts allocating
at a block picked from its metadata's checksums, moves forward through every free
block and wraps round, so all of its 80 blocks are used in turn whatever is
stored. Small files are kept inside the directory's own blocks and move nothing,
which is why the 200 rewrites above left the sector alone; one larger file - an
extension, a script, a web page - can reach it. Until then the tool still finds
it by its header, with the table's entry gone.

Just in front of the filesystem, at `0x3AF000` (the last 4 KB of `app0`), was tried
as well, and is not used. On Tasmota's layout it is the better place: the filesystem
keeps its 320 KB, the Wizard's only offer there ("Resize FS") refuses every size
("last `app` partition has no firmware"), and after a factory image it is reached only
by an update of 0x2D0000 bytes, as Tasmota's page upload stops 4 KB short of `app0`'s
end. But every device passes through Sonoff's layout, where `0x3AF000` is inside the
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

## Sonoff's ais SDK

The BASICR4's firmware (1.1.0, ESP-IDF 4.4.2) and the MINIR4's are a newer
Sonoff SDK than the S60's: its components are `ais_*` (`ais_factory`, `ais_ota`,
`ais_colink`) where the S60's are `esf_*`. Read from the BASICR4's update file
(its RISC-V code disassembled), it differs in what an emulated one needs:

- **The partition table is at 0xC000** (`CONFIG_PARTITION_TABLE_OFFSET`): its
  `load_partitions` maps sector 0 and adds 0xC000 where the S60's adds 0x8000, so
  its bootloader reads it there too. On the S60's flash it found no table,
  `nvs_flash_init` returned `ESP_ERR_NOT_FOUND`, and `espInit` aborted.
  [`tools/espressif/flashmap.py`](../tools/espressif/flashmap.py) finds a table at
  either place; the MINIR4's real table is at 0x8000 (its `nvs` starts at 0x9000).
- **Its identity and Wi-Fi are records of its own**, not `frame_params`
  ([`ais_records.py`](../tools/espressif/ais_records.py) has both), each a blob
  `info` that starts with the magic `0x55AA` and a version, then NUL-padded text
  at fixed places. A readout takes the Wi-Fi and identity out of them
  ([`scrub.py`](../tools/espressif/scrub.py)), and blanks whole, after its header,
  one of a version not known here.

  The **factory record** (namespace `factory`, 178 bytes, version 1 on both) is
  written at the factory by `AT+LICENSE_WRITE` from a license whose fields hash to
  its `sha256_check`; without one the firmware stays in `factory mode`.
  `aisFactoryLoadInfo` checks only the magic and version. It holds the eWeLink
  identity the device registers with:

  | Offset | Field | Size |
  |---|---|---|
  | 0 | magic `0x55AA`, version `1` | 2 + 2 |
  | 4 | chip MAC, `%02X` six times | 16 |
  | 20 | `device_model` (must be the firmware's own, `SN-ESP32C3-BASICR4-01`) | 32 |
  | 52 | `deviceid` | 16 |
  | 68 | `factory_apikey` | 40 |
  | 108, 126 | `sta_mac`, `sap_mac` | 18 each |
  | 144 | `uiid` (138 on both, by SonoffLAN's list) | u16 |
  | 146 | SHA-256 of `factory_apikey`, `sta_mac`, `sap_mac`, `device_model`, the uiid in decimal, `deviceid` and the chip MAC, run together | 32 |

  The **pair record** (namespace `pair`) holds the Wi-Fi it was paired with and
  where it finds eWeLink; it counts as paired when the SSID is set. Two versions
  are known:

  | Field | Version 1: 142 bytes (the BASICR4's 1.1.0) | Version 2: 176 bytes (the MINIR4's 1.2.0) |
  |---|---|---|
  | magic, version | 0, 2 + 2 | 0, 2 + 2 |
  | dispatch port | 4, u16 | 4, u16 |
  | dispatch host | 6, 32 | 6, 65 |
  | SSID | 38, 33 | 71, 33 |
  | password | 71, 65 | 104, 65 |
  | not read | 136, 6 | 169, 7 |

  Version 1's places were read from the BASICR4's code (`aisPairLoadInfo`, and
  what reads it: the cloud connection, the Wi-Fi join). Version 2's came from an
  emulated MINIR4, which was given a version 1 record and wrote it again as
  version 2 (`pair info data ver too old` ... `pair save info data ok` on its
  serial port): its host given 65 bytes, the SSID seen at 71. Given a version 1
  record holding a made-up password, it wrote the password at 104 (and logged
  `pair info: ssid[...] pwd[...]`), NUL-padded; 65 bytes fits, as in version 1,
  though no password longer than 64 characters can show whether the field runs
  on into the 7 bytes after it, which were 0. The scrub also removes every copy of
  a password ESP-IDF's own `nvs.net80211` holds, wherever it is.
- **It boots in normal mode only when nobody answers** the `factory?` it sends three
  times on its serial port with `factory!`: a production jig's.
- **Its settings' namespaces**, none of them the S60's but `phy`: `factory`, `pair`,
  `pair_bkp`, `pair_run_param`, `ewelink_device`, `outlets`, `scene`, `slisaon`.
  The donor's `frame_params` and the rest mean nothing to it.
- **Its dispatch** is `GET https://<host>:<port>/dispatch/device`, no body, through
  `esp_http_client` with the pinned CA. Its handler copies the reply only when it is
  not chunked and under 128 bytes; it then needs `error` 0, `IP` as a string, and
  `port` as a string (with none it leaves the port out), and makes
  `wss://<IP>[:<port>]/api/ws`. A reply it cannot parse leaves that URL empty, and
  its connection loop dispatches again every 2 seconds, saying nothing.
- **No secure boot or flash encryption** code is in its app, nor in the S60's (no
  `secure_boot` or `flash_encrypt` strings, where a build with them has both), and
  its updater (`ais_ota`) takes the wrapped bridge (`"error": 0`) once it is not
  overheating.
- **The BASICR4's 1.1.0 says nothing on its serial port** past `normal mode`; the
  MINIR4's 1.2.0 logs each step of its cloud connection (`http request: ...`,
  `Get WsUri OK!`, `WEBSOCKET_EVENT_DISCONNECTED`), which is how its `-v1` host and
  certificate check were found.
- **It turns down updates while overheating.** A `TEMPERATURE_CHECK` task reads
  ADC1 channel 3 (GPIO3) through `esp_adc_cal`, in millivolts: at start-up 3
  readings 300 ms apart, over 12 rounds; later 10 readings a second apart. All
  under 580 mV, it posts `TEMP_EVENT` 0, whose handler sets a flag (`0x3fca0649` in
  1.1.0) and an LED pattern (13); while the flag is set, `upgrade` is answered
  `"error": 407`. A low reading meaning hot is an NTC divider to ground; no
  published pin map names GPIO3, so it is not seen on a board yet. Neither the
  Tasmota template nor the ESPHome config this tool installs reads GPIO3, so a
  converted BASICR4 would lose this protection. The emulated ADC read 0 V, with no eFuse
  calibration 0 mV: the emulator gives it room temperature (`adc_readings` in
  [`kinds.py`](../tools/autoflash/kinds.py), a QEMU patch) and every emulated
  ESP32-C3 the calibration real ones carry.
- **On the classic ESP32 only the NSPanel reads its ADC.** Its firmware alone links
  `esp_adc_cal`, for its room thermistor (GPIO38, ADC1 channel 2), and nothing in it
  acts on the reading beyond reporting it. The other classic devices' firmwares have
  neither `esp_adc_cal` nor a temperature check, by their strings (a raw read can't be
  ruled out that way, but the POW Elite, POWCT, TH Elite and Dual R3 convert both ways
  in the emulator as it is). These firmwares are built on ESP-IDF v4.1.1, by their app
  descriptions or source paths. The fork's classic ADC (`esp32_sens`) reads 0 on every
  channel, as only PICSimLab sets it, and the emulated eFuses carry no calibration.
  With neither a measured reference voltage (block 0) nor two points (block 3),
  `esp_adc_cal` uses the reference the firmware passes, so a raw 0 reads as the
  attenuation's offset, 75 to 142 mV on ADC1, not 0 mV. The emulated NSPanel then
  sends eWeLink `"temperature": -2`, with no other effect seen
  ([the NSPanel's research](../devices/sonoff-nspanel/research.md#its-room-temperature)).
  A Beken device's ADC is in [Beken research](beken-research.md#0x3e0000--factory-tlv-store-mac--radio-calibration).
- **Its JSON parser takes only compact JSON** (`"error":0`, not `"error": 0`): with
  spaces its dispatch failed silently, over and over.
- **It turns down an EC CA** where it pins eWeLink's RSA one (`unknown_ca`).
- **Flash and other Sonoff ESP32-C3 builds.** By their image headers the BASICR4's,
  the S60's and the MINIR4's apps are all for 4 MB of flash (the BASICR4's at 80 MHz,
  the S60's at 40 MHz), so the table did not move for a larger flash. Of the other
  Sonoff ESP32-C3 update files published, the MINI-2GS's, MINI-DIM's and MINIR4M's
  are Matter OTA images (magic `0x1BEEF11E`, with Sonoff's 100-byte wrapper inside;
  the app starts 178 or 330 bytes in), built with ESP-IDF 5, whose
  `load_partitions` is not the code looked for here: their table's place is not
  known. 0xC000 is ESP-IDF's place for a table when the bootloader needs more
  than 32 KB (secure boot, flash encryption) and the default in Espressif's Matter
  SDK; that the BASICR4 was built from Sonoff's Matter-era project for that reason
  is a guess.
- **The MINIR4 puts `-v1` into its dispatch host** (`-v1%s`), and **connects by
  dispatch's `domain`**, checking the certificate against it: ESP-IDF 4.4's mbedTLS
  cannot match an IP address in one, so the stub gives a name there.

## No restart command

Sonoff's firmware has no command that only restarts the device, over the eWeLink
cloud or on the local network: a device left on it that has stopped answering is
switched off and on by hand. Every other firmware a conversion leaves a device on
part way can be restarted by the tool
([`restart.py`](../tools/autoflash/restart.py)).

Read from the S60's stock 1.2.0 (strings and the app image's segments, not
disassembled), on a copy of its dump:

- **The cloud.** The WebSocket's `action` values, listed together at file
  offset `0x39a84`, are the whole set: `register`, `update`, `upgrade`, `date`,
  `notify`, `addNotify`, `deleteNotify`, `testNotify`, `resetDispatch` and
  `redirect`. Only `upgrade` restarts the device, after writing new firmware;
  `resetDispatch` and `redirect` reconnect it to the cloud, and need it
  connected to begin with.
- **The local network.** eWeLink's LAN control: `/zeroconf/` on port 8081,
  announced as `_ewelink._tcp`, taking the same `params` as the cloud
  (`switch`, `pulse`, `startup` and so on). Its routes go through a framework
  rather than plain string compares, so they cannot all be listed from strings;
  none is a restart.
- **Nowhere else.** No `reboot` or `restart` is a command key or value anywhere
  in the image: the only such strings are ESP-IDF's own crash, watchdog and
  update logs. A factory reset (the button, or `AT+RESET=` on the serial port)
  wipes the pairing and settings.

Not traced to its handler: the `params` key `reset` (`0x26514`), listed with the
energy keys (`startups`, `totalKwh`, `getEventLog`), so most likely the energy
counters' reset. Settling it, or listing every LAN route, takes disassembly of
the handlers: a few hours each, and neither can add a restart command, as there
is no such token. The S61's firmware is the same:
[Beken research](beken-research.md#no-restart-command).

## The update path

A stock device takes its conversion as an update: an app inside Sonoff's 100-byte
wrapper, which its updater refuses unless the wrapper names its own firmware
([below](#learning-the-firmware-name)).

The emulator cannot run Sonoff's official update file as eWeLink serves it: every
Sonoff app it runs has Bluetooth start-up skipped and the stub's CA swapped in. So
the stub offers the device's own app made so (`stub_cloud/firmware.py`), as eWeLink's
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
  something else. So "no" in the package does not mean the device has none. An ESP32-C3
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
- **Reading a device and its firmware**, in [`tools/espressif/`](../tools/espressif/README.md):
  `flashmap.py` (what a dump holds: its chip, partition table and MAC), `layouts.py`
  (the tables Sonoff's firmware comes on), `sonoff_identity.py` (a device's identity,
  and `from_account`), `device_facts.py` (what a backup logs), and
  `build_vendor_ota.py` / `analyze_vendor_ota.py` (the 100-byte wrapper a stock
  updater takes).
- **eWeLink**: `tools/ewelink/firmware_name.py` (the firmware name), the stub's
  `stub_cloud/firmware.py` (the device's own app offered as eWeLink's latest), and
  `firmware_findings.py` (what Sonoff's firmware says of its hardware, a detector
  each).
- **Reading an update file's code**, as the BASICR4's was read. Strip the 100-byte
  wrapper, split the ESP image into its segments (each a load address and bytes),
  and make each an ELF with `riscv32-esp-elf-objcopy -I binary -O elf32-littleriscv
  -B riscv --rename-section .data=.text,code,alloc,load,contents`, then
  `riscv32-esp-elf-objdump -d --adjust-vma=<its address>`; the DROM segment
  (`0x3c...`) holds the strings, so each `lui`/`addi` pair's address can be named.
  An `ESP_ERROR_CHECK` that aborts names its file, line and function: its call to
  `_esp_error_check_failed` carries the line in `a2`, which finds the call. The
  table's place is the constant `load_partitions` adds after
  `spi_flash_mmap(0, 0x1000, ...)` (`lui a5,0x8` or `0xc`); the app description
  (the first segment's start) says the ESP-IDF version. PlatformIO's toolchain
  (`~/.platformio/packages/toolchain-riscv32-esp`) has both tools.
- **Watching a running emulated device**: gdb on the device QEMU runs, without restarting
  it ([the emulator's README](../tools/espressif/emulator/README.md#debugging)).

## Questions still open

- What the M5 waits for after `factory?`. Neither input pin levels nor eWeLink's model
  number for it (160 to 162) moved it. It runs on a donor flash from an update file; the
  only whole M5 dump published is the encrypted Matter one (above), so it has no
  plaintext dump to compare against yet.
- The BASICR4's whole partition table: only where it is (0xC000) is known.
