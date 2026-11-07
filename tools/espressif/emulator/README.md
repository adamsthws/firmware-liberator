# The Espressif emulator

How the emulator for Espressif plugs, the ESP32-C3 and the classic ESP32, is put
together and what it changes, for working on it. How to use it is in
[docs/emulator.md](../../../docs/emulator.md).

## How it fits together

```
 your browser ── localhost:8080 ─┐
                                 │  container autoflash-emu ("your computer")
 ┌───────────────────────────────┼──────────────────────────────────────────┐
 │  browser-forward ─────────────┘                                          │
 │  autoflash/cli.py, nmcli stand-in, wifi / plug commands                  │
 │        │                                                                 │
 │  tap0 192.168.60.1/24 ── dnsmasq (DHCP + DNS; plug fixed at .50)         │
 │        │                    └─ internet: only with --cloud (iptables)    │
 │  QEMU (lcgamboa fork) ── emulated Wi-Fi ── open AP PICSimLabWifi         │
 │        └─ flash.bin / efuse.bin in captures/emulation/state/             │
 └──────────────────────────────────────────────────────────────────────────┘
```

- **QEMU.** Espressif's QEMU has no Wi-Fi; the emulator uses
  [lcgamboa's fork](https://github.com/lcgamboa/qemu) (branch `picsimlab-esp32`),
  which emulates the Wi-Fi hardware of the ESP32-C3 and the classic ESP32, and
  offers three open access points. The image holds both of its builds,
  `qemu-system-riscv32` and `qemu-system-xtensa`. Its Wi-Fi is wired to a tap
  device in the container. The fork's
  source at the pinned commit (`64aed50`), with the subprojects its build
  fetches, is an archive on a release of this repository named after that
  commit
  ([`emulator-source-qemu-64aed50`](https://github.com/adamsthws/firmware-liberator/releases/tag/emulator-source-qemu-64aed50));
  `emu.sh` downloads it once into `captures/emulation/cache/`, checks it
  against the SHA-256 in `emu.Dockerfile`, and the image is built from it with
  no network - so building needs nothing outside this repository. Releases
  of this repository are immutable, so such a release holds one archive and
  never changes: moving the pin is a new archive on a new release, and the old
  one stays for older checkouts (the Beken emulator is kept the same way,
  `emulator-source-beken-<commit>`; the earlier `emulator-source` release
  was deleted on 2026-10-29, and its name cannot be used again). QEMU is
  GPL-2.0: the archive is its unmodified source, and this project's changes
  to it are the patches in `tools/espressif/emulator/patches/`, so a change to the
  emulator is a new patch, not a new release. The image is labelled with
  a hash of what it is built from (`tools/espressif/emulator/image.sh tag`):
  `emu.Dockerfile`, `patches/` and `container/`, so any change to them, a
  comment included, is a new image for everyone. The
  repository's `emulator-image` workflow publishes each one as
  `ghcr.io/adamsthws/autoflash-emu:<that hash>`: `emu.sh` pulls that, and
  builds only where it is not published (`AUTOFLASH_EMU_NO_PULL=1`, or
  `--rebuild`, always builds). Either way it runs only an image labelled
  with its own checkout's hash.
- **The home network.** `tap0` is the home network, and the container is its
  router: dnsmasq hands out addresses (the plug's MAC always gets
  192.168.60.50), and mDNS discovery works as on a real LAN. Without
  `--cloud`, iptables stops everything in the container reaching the
  internet, so the cloned identity cannot be used by mistake.
- **Wi-Fi scans.** The tool lists networks with `nmcli`; a stand-in reports
  the fork's access points, plus the plug's own network while it is
  broadcasting one.
- **The browser.** `browser-forward` takes localhost:8080 to the plug,
  wherever it is (see the Referer check below).
- **The firewall and sudo.** As on a typical Linux desktop, ufw is on and
  blocks incoming connections, and sudo asks for your password (`autoflash`). So
  the conversion has to add its temporary rule for the plug, through sudo,
  and remove it again, and any exit path that leaves it behind shows up. ufw
  allows only what the emulator itself needs: the plug's DHCP and DNS, and
  port 8080 for your browser. The tool checks for ufw with
  `systemctl is-active ufw`; the container has no systemd, so a stand-in
  answers from ufw's own setting. The emulator's `wifi` command needs no
  password, as a desktop's Wi-Fi menu does not. `--no-firewall` leaves ufw
  off.
- **Power.** `plug-supervisor` keeps QEMU running and starts it again when it
  exits, which is how a power cut looks.

## What is changed, and why

### The dump (`patch_stock.py`, `rewrite_nvs.py`)

Copies only; the original dump is never written. `patch_stock.py` changes
every app slot that holds Sonoff's firmware, after checking the app's
checksum and SHA-256, and seals it again afterwards. It finds each place to
change in that firmware itself, not at a fixed address, so the same code works
on every device. A firmware whose Bluetooth start-up, or pinned CA with
`--stub-cloud`, cannot be found stops with an error, rather than boot
half-changed.

- **Bluetooth start-up is skipped.** Neither QEMU emulates Bluetooth: the
  controller's clock set-up fails and its ROM code faults. The functions that
  call `esp_bt_controller_init` are made to `return 0` at once.
  `esp_bt_controller_init` is found as the one function holding the address of
  its own name, for its asserts: through an `l32r` from a literal pool on a
  classic ESP32, a `lui` and `addi` on an ESP32-C3. Bluetooth is only used for
  first-time pairing in the eWeLink app, which a plug being converted has
  already done.
- **The DPORT stall is skipped, on a classic ESP32.** An ESP32 before revision
  3 has a bug in reading DPORT registers, so its firmware stops the other core
  around each read and waits for its answer. QEMU has no such bug, and the
  other core can be too slow to answer, which trips the interrupt watchdog at
  start-up. `esp_dport_access_stall_other_cpu_start` is the one function that
  holds the addresses of both cores' interrupt registers (`0x3FF000E4` and
  `0x3FF000E8`); it, and the `_end` function straight after it, return at once.
  Firmware on ESP-IDF 5 is left alone: its `esp_ipc_isr` uses the same
  registers to stop a core it needs stopped.
- **With `--stub-cloud`, the plug trusts the stub.** eWeLink's CA, pinned in
  the firmware, is found by its issuer (the older RSA `coolkit` one, or the
  newer `CoolKit Root CA ECC`) and replaced, in its fixed 1040-byte slot, with
  the stub's own CA. That CA is EC P-256, as an RSA-2048 one would not fit.
  Where a classic ESP32's firmware pins the RSA CA, the stub's is RSA-1024
  instead (`make_ca.py --rsa`, kept in `captures/emulation/stub-cloud-rsa/`):
  that firmware gives the task holding the connection only the stack an RSA
  check takes, and on the POWCT an EC one overflowed it
  (`A stack overflow in task colink task`). OpenSSL on Ubuntu refuses an
  RSA-1024 CA by default, so the tool inside the emulator runs with
  `OPENSSL_CONF` pointing at the `openssl.cnf` `make_ca.py` writes, which
  lowers it to `SECLEVEL=1`; nothing outside the container is changed. See
  [cloud-stub.md](../../../docs/cloud-stub.md).
- **The home Wi-Fi becomes the emulator's.** Stock 1.2.0 keeps the network it
  joins in two places: ESP-IDF's `nvs.net80211` (`sta.ssid`, `sta.pswd`) and
  Sonoff's own `frame_params` (`ssid`, `passwd`). Both are rewritten to
  `PICSimLabWifi` with no password, so the real home Wi-Fi is not in the
  emulated plug at all. The partition is rebuilt with ESP-IDF's
  `nvs_partition_gen`, which adds `sta.apinfo`, `sta.pmk` and `sta.apsw` itself.
  A dump taken after the plug was reset has no network or dispatch address in
  `frame_params`, and would wait to be paired: they are added.
- **The eFuse dump needs no conversion.** QEMU's ESP32-C3 eFuse file is the
  read registers `EFUSE_RD_WR_DIS_REG` to `EFUSE_RD_SYS_PART2_DATA7_REG` in
  order, which is what `efuse.bin` holds; it is only padded to 1024 bytes. A
  classic ESP32's is its four blocks' read registers, 124 bytes, used whole.
  The plug's MAC comes from it.
- **A test copy's eFuses hold its MAC and little else.** `public_dump.py`
  writes them, as a public dump has none. An ESP32-C3's also carries the wafer
  revision its app's header asks for (byte 14, the lowest chip revision it runs
  on), or the bootloader stops at `This chip is revision 0`. A classic ESP32's
  says revision 0, which its firmware accepts.

### The QEMU fork (`tools/espressif/emulator/patches/*.patch`)

Each patch is `git format-patch` output - one change, its reasoning in its
header - applied in name order, each on top of those before it, when the
image is built. To change one, apply them all to the fork's source, make the
change, and regenerate them. What they do:

- **`0001-monitor-main-loop-context.patch` - QEMU's monitor answers.** The
  fork's main loop iterates a context of its own, but the monitor registers its
  handlers with GLib's default one, which nothing iterates: without this
  `-monitor` opens its socket and never accepts a connection. Offered upstream
  as [lcgamboa/qemu #18](https://github.com/lcgamboa/qemu/pull/18).
- **`0002-access-points-off.patch` - the router can be restarted.** The fork's
  access points live inside QEMU, so blocking traffic outside it only cuts the
  plug off from the computer; its Wi-Fi stays up. While the device's
  `access-points-off` property is set (`plug router off`, over QEMU's monitor),
  they send no beacons and answer nothing, and deauthenticate the plug - a few
  times, and again whenever it sends data, as a real access point does to a
  station that is not joined. The plug sees its Wi-Fi go within a second or two,
  retries as a real one does ("Connect failed with AP timeout"), and rejoins
  within seconds of `plug router on`. To cut the plug off from the computer
  instead, block its traffic in the container
  (`iptables -I INPUT -s 192.168.60.50 -j DROP`). Offered upstream as
  [lcgamboa/qemu #19](https://github.com/lcgamboa/qemu/pull/19).
- **`0003-esp32-cache-refresh-only-changed-pages.patch` - a classic ESP32's
  flash operations are quicker.** Each time a classic ESP32's flash cache was
  enabled or flushed, the fork threw away the translated code of its whole
  4 MB window. Now only a page whose bytes changed is refreshed: Tasmota boots
  in about 7 seconds instead of 10. Offered upstream as
  [lcgamboa/qemu #20](https://github.com/lcgamboa/qemu/pull/20).
- **`0004-esp32-cache-window-stays-mapped.patch` - a classic ESP32 is quick
  enough not to trip its watchdog.** Each flash operation also took the window
  out of the memory map and put it back, so a 32-byte flash read took several
  milliseconds of the firmware's time instead of microseconds. Firmware that
  reads flash a lot in one go then outlasts its 5-second task watchdog and
  panics: Tasmota does, reading 2,000 values in one Berry command. A window now
  stays mapped once on: Tasmota boots in about 3 seconds, and the tool reads
  its settings backup in about 6 instead of 70. It gives up QEMU's fault for
  reading flash with the cache off, so it is not offered upstream;
  [espressif/qemu #183](https://github.com/espressif/qemu/pull/183), not yet
  merged, makes the switch fast and keeps the fault.

**When you next move the pin:** lcgamboa merged #18, #19 and #20 on 7 November
2026 (`2d075b740`), so `0001`-`0003` drop out on a commit after that. Replace
`0004` with [espressif/qemu #183](https://github.com/espressif/qemu/pull/183)
(or with lcgamboa's copy of it, once it is there): it is written for Espressif's
`esp32.c`, so its changes to that file go into `esp32_picsimlab.c`, and it touches
the same part of `esp32_dport.c` as #20. Then check that Tasmota still boots in
about 3 seconds and reads its settings backup in about 6, and that ESP-IDF's
panic test `test_cache_error` still ends in "Cache disabled but cached memory
region accessed".

Tried and dropped for the classic ESP32: QEMU's multi-threaded mode (MTTCG),
which did not help and was slower; stretching the firmware's watchdogs,
which only hid real hangs for longer; and the two cores taking turns every
millisecond instead of every 100, which made no difference once flash
operations were quick.

What else the emulator needed from the fork is
[upstream now](../../../docs/upstream-contributions.md#the-emulators-wi-fi): real addresses on
the wire, frames written only where the hardware would put them, a signal
strength with its minus sign, and the computer joining the plug's own network
while the plug also tries the home one, on a link of its own with its own MAC
(02:53:36:30:00:02).

### How QEMU is run (`container/bin/plug-supervisor`)

- **The chip's QEMU.** `emu.sh` passes the chip it read from the flash, and
  the PSRAM the kind asks for, as `CHIP` and `PSRAM`; the supervisor picks the
  binary, machine, eFuse device and Wi-Fi device from the first, and gives the
  second as `-m` ([Classic ESP32 plugs](../../../docs/emulator.md#classic-esp32-plugs)).
- **`softap-status`: the plug's own network is visible.** QEMU's output passes
  through it, and it keeps the name of the network the plug broadcasts, from the
  fork's `guest SoftAP` lines, in a file the `nmcli` stand-in and `wifi join` read.
- **`gpio_in`: the button is not held down.** Run on its own, the fork drives no
  input pins, so they all read low. The S60's button (GPIO9) is active low, so
  Tasmota saw it held, and after 40 seconds reset itself to its defaults,
  losing its Wi-Fi. `-global driver=esp32c3.gpio,property=gpio_in,value=0x3fffff`
  makes inputs idle high, as with pull-ups. On a classic ESP32, `gpio_in` and
  `gpio_in1` (the pins above 31) are set all high the same way, and
  `strap_mode` is `0x12`, the strapping pins' value for booting from flash.
- **`-no-reboot`.** The fork keeps its peripherals running across a software
  reset, so firmware restarting into a different image (stock into the
  bridge, the bridge into Tasmota) crashed at start-up on what the previous
  one left behind. QEMU now exits on a reset and is started again, so every
  restart is a clean power-on. Nothing in the conversion depends on the
  reset type.
- **`-serial file:/dev/stdout`, not `-serial stdio`.** With stdin at
  `/dev/null`, the fork's stdio serial port prints nothing.
- **Only ARP requests for the plug reach it** (a `tc` filter on `tap0`, in
  `entrypoint.sh`). When the plug does not answer, the conversion tool looks
  for it by pinging every address on the network, and that burst of ARP
  requests stops the fork's Wi-Fi receiving for good: the plug keeps sending,
  but hears nothing until it is restarted. A real plug ignores requests for
  other addresses anyway.

### The browser forward (`container/bin/browser-forward`)

Tasmota serves every page except `/` only when the `Referer` names its own
address - a guard against other sites driving it. A browser at
localhost:8080 names localhost, so a plain port forward got empty replies on
every menu. The forward rewrites `Host`, `Referer` and `Origin` to the plug's
address, sends a redirect that names the plug back through localhost, and
gives each request its own connection.

## What a classic ESP32 needed

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

## Debugging

- **The serial log** is `captures/emulation/state/logs/uart.log` (`state-N`
  with `--instance N`), redacted by
  `tools/espressif/emulator/redact.py`, which masks the dump's secrets and drops panic
  memory dumps (they show RAM as words the masking cannot see into). Put any
  other log through it before reading it.
- **A QEMU of your own build**: `AUTOFLASH_EMU_QEMU=path/to/qemu-system-riscv32
  tools/espressif/emulator/emu.sh ...` runs it in place of the image's, without
  rebuilding the image; for a classic ESP32, give a `qemu-system-xtensa`.
  `qemu-build.Dockerfile` builds the RISC-V one from a fork checkout with the
  patches applied; for the Xtensa one, add `xtensa-softmmu` to its
  `--target-list` and build `qemu-system-xtensa`. Patches in
  `tools/espressif/emulator/patches/` apply in name order, each on top of those
  before it.
- **The QEMU monitor** is at `/run/autoflash-emu/monitor.sock` in the container.
- **gdb** (PlatformIO's `riscv32-esp-elf-gdb`, or `xtensa-esp32-elf-gdb` for a
  classic ESP32) against QEMU's `-s` needs a
  pseudo-terminal (run it under `script -qfc`), and hardware breakpoints
  (`hbreak`): the bootloader overwrites software breakpoints in IRAM when it
  loads the app. For the bridge's symbols, build its ELF with
  `pio run -e autoflash-bridge` in `archive/bridge`.
- **Without a NIC** (`-nic none`) the fork does not map its Wi-Fi device, and
  firmware hangs in Wi-Fi initialisation. That is a test set-up problem, not
  a firmware one.
- **The image's build environment** alone is `tools/espressif/emulator/qemu-build.Dockerfile`,
  handy for building the fork incrementally while working on a patch.
