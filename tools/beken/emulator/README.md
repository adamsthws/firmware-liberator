# The Beken emulator

How the emulator for Beken plugs, the BK7238, is put together and what it changes,
for working on it. How to use it is in
[docs/emulator.md](../../../docs/emulator.md#the-beken-emulator).

## How it fits together

`beken-emu.sh plug` runs the public S61s dump as a plug provisioned on a home
Wi-Fi, on a Docker network of its own (`autoflash-beken-emu`, apart from the
Espressif emulator and everything else). The fork's `--net-tap` puts the plug's
radio on a TAP device, which `plug-net.sh` bridges with the container's
interface; the container is that Wi-Fi's router, `.1`: DHCP (the plug at
`.50`-`.59`), DNS (every name the router), and eWeLink, the local stub
(`tools/beken/emulator/cloud_stub.py`), which every connection the plug makes off the
network is sent to - nothing reaches the internet. `tools/beken/emulator/plug.py` is
the plug itself: it boots from its flash, kept in
`captures/emulation/beken/state/` with its logs and the stub's CA, so it
carries on across firmware - each restart the firmware asks for a boot of its
own, from the bootloader, as on a real plug - and Sonoff's firmware is helped
online (`cloud_online.StockHooks`) only while it is the one that starts.

Each command first ensures the image: one labelled with the hash of what it is built
from (`image.sh tag`: `UPSTREAM`, `patches/`, `requirements.txt`,
`bk-emu.Dockerfile`), pulled from `ghcr.io/adamsthws/autoflash-beken-emu:<hash>`,
which the `beken-emulator-image` workflow publishes for every version pushed,
or built where it is not published - from the upstream source archive on this
repository's release
[`emulator-source-beken-d9a3772`](https://github.com/adamsthws/firmware-liberator/releases/tag/emulator-source-beken-d9a3772),
one archive named after its commit and never changed (`git archive` of the
pinned commit, which `image.sh source` reproduces byte for byte), downloaded
once into `captures/emulation/cache/` and checked against the SHA-256
`UPSTREAM` pins. So the emulator needs nothing outside this repository.
`beken-emu.sh` with no argument lists the rest; what each
patch does and why, how to change them, and how the pin moves, is
[below](#the-fork).

## What it models

- Base: **BekenEmulator** (`openshwprojects/BekenEmulator`), a Python emulator on
  Unicorn (QEMU's CPU core). It models the BK7238 peripherals and a RivieraWaves
  Wi-Fi MAC, and can attach a virtual 802.11 peer + user-mode IP network
  (`src/wifipeer.py` + `src/netstack.py`) — enough that stock firmware reaches a
  cloud TLS handshake over the station path.
- The BLE controller **registers** are modelled (`src/blecore.py`) and host
  commands complete, but there is **no BLE radio**: nothing is transmitted and no
  peer can connect.
- BK7238 CPU is ARM968E-S (ARMv5TE), which Unicorn runs directly.
- The **SAR ADC** is modelled (`src/emulator.py`, `src/periph.py`): a channel no
  one set reads 330 raw (about 1.06 V); `--adc CH:VALUE` or `--adc P23:1.65V` on
  `main.py` sets one. Channels 7 and up (7 is the internal temperature sensor)
  give no samples unless set. `beken-emu.sh` sets none, and the Beken plugs need
  none: unlike the ESP32-C3's BASICR4 (`adc_readings` in `tools/autoflash/kinds.py`),
  neither firmware acts on a reading ([Beken research](../../../docs/beken-research.md#0x3e0000--factory-tlv-store-mac--radio-calibration)).

Run the S61 dump:

```sh
tools/beken/emulator/beken-emu.sh run captures/<S61 dump>.bin --wifi-peer auto
# which is, in the container:
python /opt/beken-emulator/src/main.py <S61 dump>.bin --with-boot -chip BK7238 --only-uart --wifi-peer auto
```

Always pass `--only-uart`. Without it the emulator logs every MMIO access
(300k+ lines) and runs so slowly it never progresses past init.

## Booting the converted firmware in the emulator

The emulator runs OpenBeken. Booting `OpenBK7238_QIO_<ver>.bin` (OpenBeken's full
flash) comes up to its application layer — `OpenBK7238, version 1.18.319`, config
load, MQTT registration, and the config SoftAP. And the **conversion** itself boots:
splicing OpenBeken's app (from the QIO, CRC-striped) over the stock app partition while
keeping the **stock bootloader** and booting it gives

```
RT-Thread OTA package(V0.2.8-beken-...) init success   ← the stock S61 bootloader
go os_addr(0x10000)                                     ← jumps to the app
SDK Rev: 3.0.78 / OSK Rev: F-3.0.51                     ← OpenBeken's build (stock is 3.0.70)
OpenBK7238, version 1.18.319
Main_Init_After_Delay done
softap ssid=OpenBK7238_<id> channel=1 cipher=OPEN       ← OpenBeken's config AP is up
```

(The `<id>` OpenBeken appends is derived from the device identity in the dump and is
redacted here, like every other per-device value in these notes.)

So the stock bootloader boots the OpenBeken app end to end (this can also be shown
directly by splicing OpenBeken's app over the stock app partition and booting).

### The emulator performs the whole OTA apply

The emulator reproduces the full apply itself — staged OpenBeken RBL in the download
partition → erase → decompress → program → `part_fw_hash_verify` → jump → OpenBeken
boots — once it models two pieces of flash hardware the bootloader relies on only when
reprogramming its own flash:

- **The SPI flash JEDEC id (`REG_FLASH` RDID result at `0x803010`).** The bootloader
  derives the total flash size from it (`2 << ((id & 0xff) - 1)`); an unmodelled
  register reads 0, so the size is 0 and *every* bootloader write fails its
  `(addr + len <= size)` bounds check and is silently skipped — the apply then logs
  success having written nothing. Modelled to return an id whose capacity byte matches
  the image size (and whose maker/type match the bootloader's chip table).
- **XIP write-through.** The memory-mapped flash image is loaded once at setup; real
  hardware reads it live through the controller's 32+2 CRC framing, so each
  program/erase is mirrored into the XIP map de-striped. Without this the hash-verify
  (and the jump) read the stale pre-apply bytes.

Both are gated behind an emulator flag enabled only when emulating self-reprogramming
firmware (modelling real flash writes is correct but makes flash-writing firmware run
slower), so normal device emulation is unaffected. With it on, the apply completes and
OpenBeken boots exactly as above; the written app de-stripes byte-for-byte to the
OpenBeken image and its FNV equals the RBL `raw hash`.

## The fork

The emulator is upstream BekenEmulator at the commit `UPSTREAM` pins - the
archive on the release `image.sh release` names, whose hash `UPSTREAM` also pins -
with these patches applied in name order when the image is built
(`image.sh ensure` builds or pulls it; `beken-emu.sh dump` fetches the public
S61s dump from upstream's repository; `beken-emu.sh test` runs upstream's
self-tests under them, fetching its other devices' dumps the same way). Each is one
commit's worth, with its reasoning in its header.

- **`0001` - monitor-mode RX delivery** (`src/wifimac.py`): sniffed frames
  reach the RX descriptors in monitor mode even when the scan state machine is
  not active, as the AirKiss path needs. Opt-in (`monitor_rx_deliver`), so
  nothing else changes.
- **`0002` - the SPI flash's Unique ID** (`src/spi_flash.py`, new, wired in
  `src/emulator.py`): the [boot fix](../../../docs/beken-research.md#boot). Without it the SDK's UID read spins
  and the watchdog reboots the chip.
- **`0003` - OTA self-reprogramming** (`src/emulator.py`): the flash JEDEC id
  and XIP write-through the stock bootloader needs to apply an update to its
  own flash (`flash_writethrough`, off by default), plus flash-write speedups.
- **`0004` - the 26 MHz timer group** (`src/emulator.py`, `tests/self_test.py`):
  Timer 0-2 fire at each channel's programmed period, which LibreTiny's
  FreeRTOS tick needs; OpenBeken's watchdog feed and the stock dump are
  unchanged.
- **`0005` - the device's Ethernet on a host TAP device** (`src/tapnet.py`,
  new, `src/emulator.py`, `src/main.py`, `tests/tapnet.py`): `--net-tap
  IFACE` bridges the Wi-Fi peer to a TAP device instead of the built-in
  network, which is the device's router itself and reachable only through
  port forwards. The device is then a station on the host's network, given
  its address by that network's DHCP server and reached at it by every
  protocol: how `beken-emu.sh plug` puts the plug on a Docker network of its
  own, where `beken-emu.sh computer` reaches it. The built-in network's own options are refused
  beside it.

To change the fork: extract the archive (`captures/emulation/cache/`), apply
the patches to it in order, make the change as a commit there, `git
format-patch` it into `patches/`, renamed `NNNN-what-it-does.patch` (a few
lowercase words, as the Espressif emulator's are), and `image.sh ensure
autoflash-beken-emu --rebuild` builds the image again from the new set (its
hash, `image.sh tag`, changed; the workflow publishes that version once it is
pushed).
Moving to a newer upstream commit: put its hash in `UPSTREAM`, and `image.sh
source` makes the new archive (`git archive` of that commit, so anyone can
reproduce its bytes), `bekenemulator-<commit>.tar.xz`, writes its name and
SHA-256 into `UPSTREAM`, and says the release to publish it as,
`emulator-source-bekenemulator-<commit>` (draft first, with the archive
attached, then publish: releases are immutable); then rework whatever patches
no longer apply.

**With the next image**, whatever brings it - a patch, or a new pin - three
things change that would each be a new image on their own, so they wait for
it, and `tests/repo/test_emulator_images.py` fails until they are done:
`bk-emu.Dockerfile` becomes `beken-emu.Dockerfile`, named after the image as
`beken-emu.sh` is (`image.sh` takes either); `UPSTREAM`, `requirements.txt` and
the Dockerfile name the script `beken-emu.sh` (`image.sh source` writes
`UPSTREAM` so); and the five patches take the name form above. The release
holding today's archive keeps its earlier name, `emulator-source-beken-d9a3772`,
after the family; `image.sh` names the next by the upstream project, and its
exception for that one goes with that pin.
