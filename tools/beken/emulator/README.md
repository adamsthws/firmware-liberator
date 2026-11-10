# The Beken emulator

How the emulator for Beken devices, the BK7238, is put together and what it changes,
for working on it. How to use it is in
[docs/emulator.md](../../../docs/emulator.md#the-beken-emulator).

## How it fits together

`beken-emu.sh device` runs the public S61s dump as a device provisioned on a home
Wi-Fi, on a Docker network of its own (`autoflash-beken-emu`, apart from the
Espressif emulator and everything else). The fork's `--net-tap` puts the device's
radio on a TAP device, which `device-net.sh` bridges with the container's
interface; the container is that Wi-Fi's router, `.1`: DHCP (the device at
`.50`-`.59`), DNS (every name the router), and eWeLink, the local stub
(`tools/beken/emulator/cloud_stub.py`), which every connection the device makes off the
network is sent to - nothing reaches the internet. `tools/beken/emulator/device.py` is
the device itself: it boots from its flash, kept in
`captures/emulation/beken/state/` with its logs and the stub's CA, so it
carries on across firmware - each restart the firmware asks for a boot of its
own, from the bootloader, as on a real device - and Sonoff's firmware is helped
online (`cloud_online.StockHooks`) only while it is the one that starts.

Each command first ensures the image: one labelled with the hash of what it is built
from (`image.sh tag`: `UPSTREAM`, `patches/`, `requirements.txt`,
`beken-emu.Dockerfile`), pulled from `ghcr.io/adamsthws/autoflash-beken-emu:<hash>`,
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
  give no samples unless set. `beken-emu.sh` sets none, and the Beken devices need
  none: unlike the ESP32-C3's BASICR4 (`adc_readings` in `tools/autoflash/kinds.py`),
  neither firmware acts on a reading ([Beken research](../../../docs/beken-research.md#0x3e0000--factory-tlv-store-mac--radio-calibration)).
- The device's **power meter**, a BL0937, is on the device's board, outside the
  emulator: `board.py` ([below](#the-devices-board-boardpy)). Nothing else on the
  board is: no button, and the relay and LEDs are only the pins the firmware
  drives.

Run the S61 dump:

```sh
tools/beken/emulator/beken-emu.sh run captures/<S61 dump>.bin --wifi-peer auto
# which is, in the container:
python /opt/beken-emulator/src/main.py <S61 dump>.bin --with-boot -chip BK7238 --only-uart --wifi-peer auto
```

Always pass `--only-uart`. Without it the emulator logs every MMIO access
(300k+ lines) and runs so slowly it never progresses past init.

## The device's board (`board.py`)

The S61's meter is a chip beside the BK7238, so `board.py` is that board.
BekenEmulator has a BL0937 of its own (`set_energy_meter`), which toggles the
pins on its 2 ms tick - 250 Hz at most, where the voltage needs 1.8 kHz - and
follows no relay; `device.py` leaves it unset. `device.py` attaches it
to the emulator it builds, from the checkout's copy in the container
(`/work`), as `device.py` itself is run: changing it needs no new image. It is
the Beken counterpart of the Espressif emulator's
[`board.py`](../../espressif/emulator/README.md#the-devices-board-boardpy).

- **The wiring is the S61's** (`WIRING`, as
  `devices/sonoff-s61/drv_autoflash_sonoff_s61.c` and `esphome.yaml` have it,
  which `tests/beken/emulator/test_board.py` holds it to): relay P26, BL0937 CF
  P8, CF1 P6, SEL P24.
- **The load.** 100 W, resistive, on 230 V mains, measured while the relay's
  GPIO is driven high (its output driver on, bit 3 of the config word clear,
  and bit 1 set), nothing while it is off. The voltage is measured on the
  supply side, so it is there either way.
- **The BL0937** gives pulses: CF at a rate that is the power, CF1 at one that
  is the voltage while SEL is driven high and the current while it is low (the
  BL0937's sense; an HLW8012's is the other way round). The rates come from the
  factory calibration in the public dump's store, as the stock firmware,
  OpenBeken's import and ESPHome's config each apply it
  ([devices/sonoff-s61/research.md](../../../devices/sonoff-s61/research.md#energy-meter-calibration-the-stock-formula-and-the-openbeken-mapping)):
  75 Hz on CF, 1794 Hz on CF1 for the voltage and 39 Hz for the current.
  OpenBeken and ESPHome count them by the GPIO interrupt, so the board drives
  those pins' levels through the emulator's own GPIO model, between its chunks
  of instructions (`_pacing`, which then raises the interrupt); and the idle
  loop is skipped only while no pulse is due, as a skip of ten chunks would
  lose one.
- **Sonoff's firmware times them instead**, with the chip's PWM block: CF1
  (P6) on PWM channel 0 and CF (P8) on channel 2 capture rising edges (mode
  4, pwm_new's `PWM_CAP_POS_MODE`) on the 26 MHz clock, and the PWM interrupt
  hands the driver each period's count
  ([how it reads them](../../../devices/sonoff-s61/research.md#how-sonoffs-firmware-reads-the-meter)).
  The emulator models that block's registers but drives nothing into a
  capture, so the firmware read nothing and reported 0 V, 0 A and 0 W. Where
  a channel's control word (`0x802B00` + 0x40 a group) has it capturing, with
  its pin the meter's, the board writes the count it latches - the clock,
  over the group's divider, in one period at the output's rate (half of one
  in edge mode) - into the channel's `RD_DATA`, and raises its interrupt
  status (`pwmn_stat`) where it is enabled; and a pulse waits for that status
  to be cleared, as for INTSTA below. The count is the period at the
  output's rate, not the time since the last pulse given, so an edge a chunk
  late does not change it.
- **A cycle is one short low pulse**, the pin falling and rising again between
  two chunks: one interrupt, whichever edge the firmware waits for - the
  falling one (OpenBeken), or each in turn, as LibreTiny's change interrupt
  switches between them (ESPHome, which counts a pulse when it reads the pin
  high in its interrupt). A square wave's two edges a cycle were more
  interrupts than the emulated CPU takes at 1.8 kHz: ESPHome read 160 V.
- **A pulse waits for the last to be taken.** One is given only once the
  firmware has cleared the interrupt the last latched (INTSTA), and a cycle not
  given yet is given as soon as it has, at most a quarter of a second's worth
  owed. A chip's edge is taken within microseconds; in the emulator a
  firmware's stretches with interrupts off last far longer in the device's
  time, and free-running at 1.8 kHz a quarter of the voltage's edges were lost:
  OpenBeken read 176 V of 230.
- **What the firmwares read**, in each way of `fault_suite.py check` (the
  end-of-run check, with the cloud stub): OpenBeken 248-250 V and 103-105 W
  with the relay on, the same voltage and 0 W off; ESPHome 244 V and 103 W on,
  244 V and 0 W off. Both read a few percent high because the firmware's clock
  falls behind the device's in the emulator: OpenBeken's uptime counted 67 s
  in 70 s of the device's time (seen by printing the device's time beside its
  log). That the voltage is further out than the power is inferred to be the
  windows it is read in, each with the voltage's 1.8 kHz of interrupts, losing
  more. Sonoff's firmware reports what the load is, to the stub, as it times
  the periods by the chip's clock rather than its own: 230.22 V, 0.43 A and
  100.00 W with the relay on (switched through the stub's eWeLink, and asked
  for readings as the app asks, `uiActive`), 230.22 V and nothing off.
- **Not modelled:** the chip's pulse shape and width (only the rate counts),
  and a pulse's timing within a chunk of 0.2 ms.

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
  protocol: how `beken-emu.sh device` puts the device on a Docker network of its
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

The release holding today's archive keeps its earlier name,
`emulator-source-beken-d9a3772`, after the family; `image.sh` names the next by
the upstream project, and its exception for that one goes with that pin.
