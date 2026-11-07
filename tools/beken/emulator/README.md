# The S61's emulator

[BekenEmulator](https://github.com/openshwprojects/BekenEmulator) at the commit
`UPSTREAM` pins, with this project's patches and the Python it imports, as a
container image - the way the S60's emulator is kept (`tools/espressif/emulator`). The
tools in `tools/beken` run in it:

```sh
tools/beken/emulator/bk-emu.sh python tools/beken/seed_join.py
tools/beken/emulator/bk-emu.sh run captures/your-dump.bin --wifi-peer auto
tools/beken/emulator/bk-emu.sh test -k "Sonoff 4MB"    # upstream's self-tests, under the patches
tools/beken/emulator/bk-emu.sh plug                     # the S61 on a home Wi-Fi of its own
tools/beken/emulator/bk-emu.sh computer tools/autoflash/cli.py run   # the tool, on that Wi-Fi
tools/beken/emulator/bk-emu.sh power cycle              # the plug switched off and on
tools/beken/emulator/bk-emu.sh reset                    # the plug as new, on Sonoff's firmware again
tools/beken/emulator/bk-emu.sh ui                       # OpenBeken's page, at http://127.0.0.1:8088/
```

`bk-emu.sh plug` runs the public S61 dump as a plug provisioned on a home
Wi-Fi, on a Docker network of its own (`autoflash-beken`, apart from the
S60's emulator and everything else). The fork's `--net-tap` puts the plug's
radio on a TAP device, which `plug-net.sh` bridges with the container's
interface; the container is that Wi-Fi's router, `.1`: DHCP (the plug at
`.50`-`.59`), DNS (every name the router), and eWeLink, the local stub
(`tools/beken/cloud_stub.py`), which every connection the plug makes off the
network is sent to - nothing reaches the internet. `tools/beken/plug.py` is
the plug itself: it boots from its flash, kept in
`captures/emulation/s61/autoflash-s61/` with its logs and the stub's CA, so it
carries on across firmware - each restart the firmware asks for a boot of its
own, from the bootloader, as on a real plug - and Sonoff's firmware is helped
online (`cloud_online.StockHooks`) only while it is the one that starts.

`bk-emu.sh computer` is a computer on that Wi-Fi: the tool there finds the
plug as on a home network, and its eWeLink is the stub. It works in its own
copy of this checkout (the plug's `work/`), so its runs' captures are its own;
an unattended run takes its eWeLink account from `work/captures/` (a made-up
one: the stub takes any). Sonoff's firmware, having fetched and staged a new
image whole and checked its CRC, restarts a second later, and the bootloader
installs it, as a real plug's does (the firmware's own code says so,
[docs/beken-research.md](../../../docs/beken-research.md)). A plug
that does not come back is switched off and on - `bk-emu.sh power cycle`, as
the tool then asks; an attended run there offers to do it itself: the plug's
power switch is passed to the computer (`AUTOFLASH_EMULATOR_POWER`).

**The plug's web page goes through a proxy** (`bk-emu.sh ui`, `ui_proxy.py`),
OpenBeken's or ESPHome's - `ui` tells which by the name the plug gave its
router - never opened straight. The emulated plug runs the real OpenBeken, but far
slower than a chip: each request leaves a connection in TIME_WAIT on the plug,
lwIP has room for only a few, and on the emulator's clock they clear slowly. A
browser's parallel requests and the page's once-a-second polling fill them in
seconds, and the plug stops answering though its firmware still runs. The proxy
lets one request at a time reach the plug, at most one every 0.8 s, answers the
page's polling from a 3-second cache, and passes every action straight on;
[`ui_proxy.py`](ui_proxy.py) says more. ESPHome's page keeps its live values
coming on one open connection (`/events`), which the proxy passes straight
through, one browser tab at a time.

Each command first ensures the image: one labelled with the hash of what it is built
from (`bk-emu.sh tag`: `UPSTREAM`, `patches/`, `requirements.txt`,
`bk-emu.Dockerfile`), pulled from `ghcr.io/adamsthws/autoflash-beken-emu:<hash>`,
which the `beken-emulator-image` workflow publishes for every version pushed,
or built where it is not published - from the upstream source archive on this
repository's release
[`emulator-source-beken-d9a3772`](https://github.com/adamsthws/firmware-liberator/releases/tag/emulator-source-beken-d9a3772),
one archive named after its commit and never changed (`git archive` of the
pinned commit, which `bk-emu.sh source` reproduces byte for byte), downloaded
once into `captures/emulation/cache/` and checked against the SHA-256
`UPSTREAM` pins. So the emulator needs nothing outside this repository. Upstream
publishes no licence. `bk-emu.sh` with no argument lists the rest; what each
patch does and why, how to change them, and how the pin moves, is
[below](#the-fork).

`AUTOFLASH_BEKEN_DUMP` names another whole flash under this checkout for a new
plug to start from, one `rebuild_dump.py` made for another model, say
([Adding a 4 MB BK7238 device](../README.md#adding-a-4-mb-bk7238-device-with-no-whole-dump)).

**No firmware is in the image or the archive.** The public S61s dump is Sonoff's
firmware, which this repository never holds, so `bk-emu.sh dump` fetches it
from upstream's repository, which publishes it, into
`captures/emulation/beken-firmwares/` (the tools do this before they run), and
`bk-emu.sh test` fetches upstream's other devices' dumps there too (about
280 MB). That directory is mounted into the container at the emulator's
`firmwares/`, and the checkout at `/work`, as your own user; `S61_DUMP` names a
dump of your own, under `captures/`.

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

Run the S61 dump:

```sh
tools/beken/emulator/bk-emu.sh run captures/<S61 dump>.bin --wifi-peer auto
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
archive on the release `emulator-source-beken-<commit>`, whose hash `UPSTREAM` also pins -
with these patches applied in name order when the image is built
(`bk-emu.sh ensure` builds or pulls it; `bk-emu.sh dump` fetches the public
S61s dump from upstream's repository; `bk-emu.sh test` runs upstream's
self-tests under them, fetching its other devices' dumps the same way). Each is one commit's worth, with its reasoning in
its header; none has been offered upstream yet.

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
  protocol: how `bk-emu.sh plug` puts the S61 on a Docker network of its
  own, where `bk-emu.sh computer` reaches it. The built-in network's own options are refused
  beside it.

To change the fork: extract the archive (`captures/emulation/cache/`), apply
the patches to it in order, make the change as a commit there, `git
format-patch` it into `patches/`, and `bk-emu.sh ensure` builds the image
again from the new set (its hash, `bk-emu.sh tag`, changed; the workflow
publishes that version once it is pushed).
Moving to a newer upstream commit: put its hash in `UPSTREAM`, `bk-emu.sh
source` makes the new archive (`git archive` of that commit, so anyone can
reproduce its bytes), publish it as the release `emulator-source-beken-<its
commit>` (draft first, with the archive attached, then publish: releases are
immutable), pin its SHA-256 and name in `UPSTREAM`, and rework whatever
patches no longer apply.
