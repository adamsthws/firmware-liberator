# Beken tooling

The tooling for Beken (BK72xx) plugs, worked out on the Sonoff S61 - see
[docs/beken-s61-emulation.md](../../docs/beken-s61-emulation.md). Not part of
the Autoflash tool itself: these drive the **BekenEmulator** with this project's
patches, read and write the flash formats a conversion passes through, or
analyse a firmware image statically. The experiments that proved how the stock
firmware pairs, and that nothing in the conversion needs (AirKiss replay, BLE
provisioning, the button and pairing drives), are in
[archive/s61-research/](../../archive/s61-research/).

**The emulator** comes with this repository as a container image, kept as the
S60's is: [`emulator/`](emulator/) pins the upstream commit and its source
archive (on the repository's `emulator-source-beken-<commit>` release) and holds
this project's patches, and its `bk-emu.sh` pulls the published image for that
version or builds it, fetches the public S61s dump from upstream's repository
(Sonoff's firmware, which this repository never holds) and runs a tool in the
container, with this checkout at `/work`:

```sh
tools/beken/emulator/bk-emu.sh python tools/beken/integrity_gate.py
```

In the container the emulator is at `/opt/beken-emulator`, the tools' default
`BK_EMU_ROOT`, and the fetched dump their default `S61_DUMP`; the image's Python
has `capstone` for the static tools too (`angr`, for `decomp.py` only, goes
into a virtual environment of your own, never the system). The examples below
leave out the `bk-emu.sh python` prefix and assume the current directory is
`tools/beken`.

## Static analysis

Work on the **logical image** (the dump with its CRC stripes removed, so a CPU
address equals a file offset). Produce it once with `unstripe.py`, then point
the other tools at it.

- **`unstripe.py`** — un-stripe a raw Beken dump to a logical image and decode
  its RBL update headers. Uses the fork's CRC routine.
  `python3 unstripe.py <raw-dump> logical.bin`
- **`disasm.py`** — disassemble a window (capstone), Thumb by default.
  `python3 disasm.py logical.bin 0x4203c 24`
- **`xref.py`** — find references to an address: BL/BLX callers over the code
  region plus 32-bit data-word refs (for `addr` and `addr|1`).
  `python3 xref.py logical.bin 0x3eba0 0x21e50`
- **`strxref.py`** — locate code by the log/format string it uses: finds the
  string, its literal-pool refs, and disassembles the `ldr` that loads each.
  `python3 strxref.py logical.bin "pairingMode by" "softap:ssid"`
- **`decomp.py`** — decompile functions to pseudo-C with angr (best-effort;
  a reading aid alongside `disasm.py`). No Java/Ghidra needed.
  `python3 decomp.py logical.bin 0x4203c`
- **`layouts.py`** — read the partition table a BK7238 app holds, and say whether
  it is one the tool knows. A run refuses a device whose official file holds any
  other, before anything is written.
  `python3 layouts.py official.bin`
- **`stock_points.py`** — find the places in a BK7238 eWeLink app where the
  emulator helps it, by what the code there refers to. The tools below use
  nothing else, so one code path runs each build. A point it cannot find, or
  could find in two places, is an error naming it. Standard library only.
  `python3 stock_points.py dump.bin official.bin app.bin`

  | Point | From what it is found |
  |-------|-----------------------|
  | `x509_crt_parse` | the function holding both `-----BEGIN/END CERTIFICATE-----` |
  | `ef_get_env_blob` | of those logging `ENV isn't initialize OK.`, the one keeping a fourth argument |
  | `after_memcmp`, `gate_passed` | the 32-byte compare after `%s%s%s%d%s%s`, whose result is tested at once |
  | `appfw_task`, `appfw_loop` | the function given to the task `ais_appfw_task`; the target of its first branch back |
  | `link_task`, `link_create`, `ais_cloud_start` | the task `ais_link_task`: its function, the call creating it, the function holding that call |
  | `start_cloud` | the one function calling `ais_cloud_start` |
  | `ctx_pointer`, `cloud_flag` | the first word in RAM each of those two reads through |
  | `lan_publish` | called straight before `start_cloud`, starting by reading a flag beside `cloud_flag` |
  | `dispatch_url`, `url_offset` | the plain `http://` URL in the function that checks `cloud_flag`; its field in the structure that function fills |

## Driving the emulator

Those marked archived are in
[archive/s61-research/](../../archive/s61-research/), and are run from there.

- **`integrity_gate.py`** — lets the stock image boot under emulation past the
  flash-UID integrity gate that otherwise hangs the app layer (see the main doc).
  Forces the identity `memcmp` to "equal" (the outcome it has on real
  hardware, whose UID the emulator's synthetic one can't match) and shows the
  firmware then boot the app framework naturally: `key_add` registers the button,
  the provisioned-check runs, and `ais_appfw_task` is created. This operates only
  inside the emulator, on a dump of the owner's own plug; it is never written to or
  run on a real plug.
  `python3 integrity_gate.py`

- **`long_press.py`** (archived) — with the gate cleared, drive the button long-press end
  to end through the firmware's own key stack. Two interventions: the integrity
  bypass, and raising Timer 5's interrupt status (`timer3_5_ctl` bit 9) each slow
  tick — the key-driver's periodic runs on the `bk_timer` = Timer 5, which the
  emulator does not raise (it raises only Timer 3, the RTOS tick). With both, the
  key-driver timer thread (`0x3eba0`) wakes naturally, its scan (`0x421e0`) runs,
  and a press-and-hold of pins 0/2 trips the real long-press action `0x42250`.
  `python3 long_press.py`

- **`pair_drive.py`** (archived) — drive the firmware into pairing mode and watch the
  emulated radio follow. With the gate cleared and the app framework cycling,
  nothing sets a pairing method, so the device idles; this tool calls the
  firmware's own pairing-method dispatcher `0x21eb4` from inside the appfw task's
  context. Method 0 (coexist) brings up **soft-AP** — the MAC leaves monitor mode
  and transmits beacons, and the virtual peer sees the device as an access point;
  method 1 enters **station** mode and probes/associates. `airkiss` instead drives
  the sniffer-start `0x223b0` (the RX-FIQ handler object goes live) — but note the
  AirKiss monitor callback `0x403b20` is only ever a **stub** in this build, so
  there is no decoder behind it (see the main doc). Shares the integrity bypass +
  Timer-5 raise with `long_press.py`.
  `python3 pair_drive.py 0` (soft-AP) `| 1`
  (station) `| airkiss`. Env `SSID`/`PASS`, `SECS`, `FIRE_AT`.

- **`cloud_push.py`** — a whole S61 conversion, the way the tool would drive
  one: the plug online against the stub (`cloud_online.CloudSession`), pushed an
  eWeLink `upgrade` for an rt_ota `.rbl` wrapped by `fota_wrap.py` and served
  from a placeholder public address the relay sends to this host; the plug
  downloads it in ranges and stages it at `0x286000`, checked byte for byte,
  and with `--apply` the next boot has the stock bootloader apply it and the new
  firmware start. The plug's Wi-Fi is written into a copy of the dump's
  EasyFlash store first, so that firmware finds it as on a provisioned plug.
  `python3 cloud_push.py image.rbl out.bin --apply` (env `SECS`, `APPLY_SECS`)

- **`stage_apply.py`** — the apply half of a conversion, or of a reversion, in
  one command: stage an rt_ota `.rbl` (OpenBeken's, the ESPHome build's, or the
  repackaged stock app's) in the download slot of a copy of a dump, seed test
  Wi-Fi credentials in its EasyFlash store, and boot the stock bootloader over it
  with the emulator modelling the flash writes; the flash as the run leaves it
  is saved, for `bk-emu.sh run OUT --wifi --wifi-peer ap ...` to show the
  installed firmware start afresh and join.
  `python3 stage_apply.py dump.bin image.rbl out.bin` (env `SECS`, `SSID`, `PASS`, `SEED=0`)

- **`seed_join.py`** — put the emulated plug on a network the faithful way.
  Hooks the firmware's own `ef_get_env_blob` (EasyFlash V4) and returns
  the tool's SSID/passphrase for `wifi-ssid`/`wifi-pass` — making it a provisioned
  plug at the credential read, with nothing else forced. Attaches the fork peer as a
  WPA2-PSK AP with the same creds; the plug then does the real sequence (station →
  associate → WPA2 four-way → **IPv4 DHCP → 192.168.1.100**). This corrects the old
  "seeding creds leaves it idle" result — that was the integrity gate, which this
  clears too. Env `SSID`/`PASS`/`CH`/`SECS`.
  `python3 seed_join.py`

- **`ble_provision.py`** (archived) — synthesise the eWeLink **BLE provisioning session**
  without a BLE radio: feed the provisioning messages (DEVICE_INFO, AUTH_TOKEN,
  HIDDEN_AP_CONNECT/Wi-Fi, SERVER_INFO) through the firmware's command dispatcher
  `0x72940`, so its own consumer task runs the handlers in context (driving them
  directly corrupts the shared state; the dispatcher does not). Data fields are
  injected plaintext past the AES (the key is the device apikey at RAM `0x40ce90`).
  The handlers run and the plug joins the tool's AP (WPA2 + DHCP). This exercises
  the real eWeLink provisioning path, but does not by itself dial the cloud — that
  needs the pairing-completion trigger (`cloud_dial.py`). Env `SEQ`/`SSID`/`PASS`/`SECS`.
  `python3 ble_provision.py`

- **`cloud_dial.py`** — the full provisioning outcome: the emulated plug dials
  the **eWeLink (CoolKit) cloud**. Seeds Wi-Fi creds (join + DHCP), then synthesises
  the pairing-success trigger by force-calling the firmware's own start-cloud
  handler (the single call the pairing state machine makes on success).
  `ais_link_task` then opens a TCP connection and sends `GET /dispatch/device`,
  retried with backoff. The dispatch target lives in the AIS context (`ctx_pointer`,
  factory placeholder `http://8.8.8.8:80`); **by default this tool repoints it to a
  local stub on the emulator's AP gateway (`http://192.168.1.1:80`), so the dial
  stays on this host.** `DISPATCH` steers it elsewhere; `DISPATCH=` keeps the factory
  placeholder. **Owner's own account only:** never point it at the real eWeLink cloud
  with anyone's account or plug but your own. Env `DISPATCH`/`SSID`/`PASS`/`SECS`/`FORCE_AT`.
  `python3 cloud_dial.py`

- **`cloud_online.py`** — complete the eWeLink cloud cycle: the plug reaches
  **ONLINE**. Stands up the CoolKit stub (`tools/espressif/emulator/stub_cloud/stub_server.py`,
  `--plain` dispatch mode) on loopback and bridges the netstack relay to it
  (plaintext dispatch + TLS IoT, fully contained — nothing leaves the host), seeds
  Wi-Fi creds and fires the pairing trigger as `cloud_dial.py` does, then drives
  dispatch → TLS `/api/ws` → `register` → `{error:0, config}` ONLINE. Two things it
  handles that the S60 does not: the S61 dispatch reply must be **compact** JSON
  with a **`domain`** key and a **string** `port`; and the IoT `wss://` cert is
  verified, cleared by a **trust-anchor swap** — the honest S60 `--trust-ca`
  technique — applied at `mbedtls_x509_crt_parse` because the S61
  assembles its CoolKit CA in RAM rather than keeping it in flash. It generates its
  own EC P-256 CA + leaf (openssl) and serves the leaf; the firmware's verify path
  is untouched. Env `SSID`/`PASS`/`SECS`/`FORCE_AT`/`HB`.
  `python3 cloud_online.py`. Its set-up is a `CloudSession`, which `cloud_push.py`
  builds on.

- **`button_sim.py`** (archived) — older button sim (predates the integrity-gate finding
  and natural registration; defaults to the wrong pin 3). Force-runs the block init
  and `key_add`, then presses by driving the GPIO low and latching the capture
  status in `state.pwmn_stat`, reaching the armed long-press timer.
  `python3 button_sim.py [pin]`

- **`airkiss_capture.sh`** (archived) — put a Wi-Fi adapter that can capture in monitor mode (an Intel AX200 can) into it on
  the router's channel and capture a phone's eWeLink AirKiss/smartconfig broadcast
  to a pcap. Needs root; takes over the Wi-Fi adapter while capturing.
  `sudo ./airkiss_capture.sh <channel> [iface] [out.pcap]`

- **`airkiss_replay.py`** (archived) — replay captured 802.11 frames into the emulated
  S61's Wi-Fi MAC (monitor mode) so the firmware's own AirKiss decoder can consume
  them; an AP-role peer with the known SSID/pass then lets the device associate.
  Stdlib-only pcap/radiotap parsing (DLT 127/105).
  - `python3 airkiss_replay.py capture.pcap --ssid MyNet --pass MyPass`
  - `python3 airkiss_replay.py --selftest`  (synthetic-frame pipeline check)

### AirKiss end-to-end procedure

1. Pick an SSID/password to provision to (a real or throwaway network).
2. `sudo ./airkiss_capture.sh <router-channel> wlp1s0 airkiss.pcap`
3. On the phone: eWeLink app → add the plug → Wi-Fi/quick (AirKiss) pairing →
   enter that SSID/password → start. Let it run ~30-60 s, Ctrl-C the capture.
4. `python3 airkiss_replay.py airkiss.pcap --ssid <SSID> --pass <PASS>`
5. Watch whether the firmware leaves monitor mode, probes/associates, gets DHCP.

## Conversion and config migration

The conversion tool's own S61 route is `tools/autoflash/routes/beken/`,
built on the pieces here: `fota_wrap` for the push through eWeLink, `rbl` and
`stock_app` for Sonoff's firmware made again from the public dump, `uf2` for
an update sent to ESPHome, `backup.factory_mac` for the backup.

Reading the stock config and carrying the Wi-Fi credentials into OpenBeken. See
[the config-migration section of the emulation reference](../../docs/beken-s61-emulation.md#carrying-wi-fi-across-the-conversion-the-openbeken-config)
for the full findings (config partition map, OpenBeken `mainConfig_t` format, the
signed-char `Tiny_CRC8`, the `0x1e1000` config offset).

- **`ef40_dump.py`** — enumerate the stock EasyFlash V4 (EF40) key-value stores
  from a flash dump, with CRC validation. Shows `wifi-ssid`/`wifi-pass`,
  `elecCalibCoef` and the rest. `python3 ef40_dump.py <flash.bin>`
- **`ef40.py`** — EF40 reader/writer library: `parse_store()` reads KV40
  nodes; `provision()` writes synthetic `wifi-ssid`/`wifi-pass` nodes (for
  building a provisioned test dump, since the public dump is join-unprovisioned).
- **`obk_config.py`** — build an OpenBeken `mainConfig_t` blob carrying given
  Wi-Fi credentials (correct ident/version/offsets, `sizeof` 3584, and the
  **signed-char** `Tiny_CRC8` the shipped binary uses). `build_config(ssid, pass)`.
- **`wifi_migrate.py`** — the offline chain: read `wifi-ssid`/`wifi-pass` from
  a stock dump's EF40 → build an OpenBeken config → write it at the config
  partition of an OpenBeken image. Version-fragile (depends on the exact config
  format); prefer the OpenBeken driver below.
  `python3 wifi_migrate.py --stock stock.bin --obk OpenBK7238_QIO.bin --out converted.bin`
- **`openbeken/drv_autoflash.c`**, with each plug's table for it
  (`devices/sonoff-s61/drv_autoflash_sonoff_s61.c`) — **the dependable mechanism to ship.**
  An OpenBeken driver that, on first boot, reads the stock EF40 creds with
  `beken_hal_flash_read` and sets them via OpenBeken's own
  `CFG_SetWiFiSSID()`/`CFG_SetWiFiPass()` — so it survives OpenBeken config-format
  changes across releases — then carries the factory meter calibration over and
  applies the plug's wiring. Reads the stock store only (reversible). Build recipe
  and the `AutoflashRevertToStock` command in [`devices/sonoff-s61/openbeken.md`](../../devices/sonoff-s61/openbeken.md).
- **`devices/sonoff-s61/esphome.md`** — the **ESPHome route**: how
  [`devices/sonoff-s61/esphome.yaml`](../../devices/sonoff-s61/esphome.yaml) (LibreTiny) reads
  the stock Wi-Fi and meter calibration from the EF40 store at boot, its 4 MB
  flash layout, the build, the reversion through ESPHome's own OTA, and what
  the emulator validated.
- **`stock_app.py`** — extract the stock app from a dump, verified against the
  FNV-1a hash in its RBL header (`extract()`), and pack it again as the RBL the
  stock bootloader applies for a revert to stock (`stock_rbl()`).
  `python3 stock_app.py dump.bin -o stock_app_1.0.2.bin`
- **`rbl.py`**, **`aes.py`** — rt_ota RBL images as the SDK's packer makes
  them: gzip, PKCS#7 and AES-256-CBC under RT-Thread's public default key, and
  the 96-byte header with its CRC-32s and FNV-1a hash. `rbl.pack()` gives the
  same body byte for byte as `rt_ota_packaging_tool_cli -c gzip -s aes`;
  `rbl.unpack()` checks and unpacks one. The AES is written out here because
  the tool runs on Python's standard library, which has none.
- **`uf2.py`** — the UF2 an ESPHome built on LibreTiny takes as an update,
  with an RBL in its `download` part for the stock bootloader to apply: byte
  for byte what `ltchiptool uf2 write --legacy IMAGE=device:download` writes
  for the S61's board. How a plug on ESPHome goes to OpenBeken or back to stock.
- **`backup.py`** — copy a converted plug's per-device flash region
  (factory TLV, EasyFlash store, system ENV; `0x3e0000-0x400000`) out over
  OpenBeken's REST API into `captures/plugs/<mac>/sonoff-backup/`, the S60's
  layout, never overwriting. `python3 backup.py --plug http://<plug> --stock-rbl stock_app_1.0.2.rbl`

## Rebuilding a dump

**`rebuild_dump.py`** makes a BK7238 plug's whole stock flash again with no
serial cable, so a device with no public dump can be run in the emulator. Not
part of the tool's run: see the steps below. Everything it reads or writes is
private, and the result is Sonoff's firmware, so it stays under `captures/`.

1. Convert the plug to OpenBeken with the stock updater, which overwrites the
   start of the stock app.
2. Read the whole flash with `GET /api/flash/<start>-<len>` (`backup.read_flash`).
3. Get the model's official OTA file: a FOTA container around an RBL of LZMA
   under the public AES key (`fota_wrap.unwrap`, `rbl.unpack`).
4. `python3 tools/beken/rebuild_dump.py READOUT.bin OFFICIAL.bin OUT.bin [--compare DUMP.bin]`

It was tried on the S61 in the emulator, with the public dump as the answer.
Reading over REST gave the plug's flash byte for byte. Each region of the result:

| Region | Against the public dump |
|--------|-------------------------|
| bootloader and partition table, radio calibration, stale sectors in the app partition, user config | the same |
| OpenBeken's config sector and other writes (`INTERIM_WRITES`) | the same once they are erased |
| app | that of the official file, byte for byte as the stock bootloader writes it. The file's version need not be the plug's: the public file is 1.1.0, the dump 1.0.2 |
| RBL header, download slot | that of the official file, as the bootloader leaves them. They come from the file, so match only at the same version |
| EasyFlash stores | differ: the plug's own history since the dump (Wi-Fi details, `ota&state`, the eWeLink keys, a Matter id) |

The rebuilt flash boots in the emulator to the point the public dump reaches:
through the stock bootloader, onto Wi-Fi and ONLINE on the local stub, under the
usual patches. With 1.1.0 from the public file it reports `romVersion` 1.1.0,
and `cloud_push.py --apply` then converts it to OpenBeken as it does the dump.

Whether the file is the version the plug ran shows in the output: OpenBeken is
shorter than the stock app, so the rest of the old app is still in the flash to
compare with. The emulator's help goes where `stock_points.py` finds it, so
another version or model needs nothing moved: each BK7238 official file of the
S61's generation, on the S61's bootloader and settings, comes ONLINE on the stub
(1.0.2 from the public dump; S61 1.1.0, BASIC-1GS 1.0.4, BASIC-1GSP 1.0.5, S41
1.0.6, T6 1.0.3 rebuilt). The door sensor's firmware is another stack: its points
are not found, and the tools refuse it by name.

Not proven: any of it on a real plug, including that the REST read returns the
raw striped flash there as it does in the emulator. The flash's unique ID is not
read at all: the integrity gate, which checks it, stays patched. Only this
project's OpenBeken build was read, soon after its first start: `INTERIM_WRITES`
assumes those places were blank before it, as they are on the S61, and another
model's may not be.

A plug on another table needs more. The official apps carry the SDK's table,
though not the bootloader's FAL one: the S41, T6 and Basic Gen5's (`BASIC-1GS`,
`BASIC-1GSP`) are the S61's 4 MB one. The 2 MB door sensor's puts `NET info` at
`0x1e1000`, where it holds data, and OpenBeken's 2 MB build writes its config
there, so a conversion would lose that sector before it could be read;
`rebuild()` refuses a table it has no `INTERIM_WRITES` for unless they are given.

## Adding a 4 MB BK7238 device with no whole dump

A device with no public dump can be added from a pin map and eWeLink's official
update file. The [Sonoff Basic Gen5](../../devices/sonoff-basic-1gs/README.md) is
the first. What makes that safe enough to offer, and what it leaves open:

- **The file's partition table is checked first.** Each app holds the SDK's table,
  as entries of five words: owner, description, start, length, options.
  `layouts.py` reads it by the description strings, and a run refuses a file
  whose table is not listed there. That happens at B1, again at B2, and when
  Sonoff's firmware is sent back. The known one is `beken-4m`: `Application`
  `0x11000+0x250000`, `ota` `0x286000+0x15a000`, `RF Firmware` `0x3e0000`,
  `NET info` `0x3e1000`, `NVS` `0x3e2000+0xb000`.
- **Stock firmware does not say its flash size.** The register message gives only
  `romVersion`, `chipid` (its MAC) and `model`; the account record's `params` add
  the Wi-Fi and the relay's settings; the firmware name ends in `BK7238`, where an
  Espressif one can end in `FN4`. The app reads the flash's ID for itself
  (`[Flash]id:` on the serial output) but tells no one. There is no LAN API on this
  route. So before a conversion the table is the only word on the flash.
- **Published specs disagree with the table.**
  [CNX Software's review](https://www.cnx-software.com/2025/11/06/review-sonoff-basic-gen5-matter-switch-and-tx-gen2-matter-smart-wall-switch/)
  and others give the Basic Gen5, the BASIC-1GSP and the T6 "2 MB flash (SiP)", the
  chip's usual figure, while their official files hold only the 4 MB table. The
  file is what the device runs, so the tool goes by it, but nobody has read the
  flash of one of these here.
- **After the conversion the tool can tell.** B4 reads the device's own data at
  `0x3e0000` through OpenBeken. On a smaller flash that read would not find the
  device's factory record, so finding it, with the device's own MAC, is logged as
  the flash matching the table.
- **The person accepts a warning.** A kind with `converted_for_real=False` asks for
  the word `UNTESTED` at B2: no real one has been converted, a Beken device has no
  fallback by switching it off and on as an Espressif one has, a failure may need a
  serial cable, and please send the log. `--unattended` accepts it, as it does the
  other go-aheads, and logs that it did.
- **Each run logs what it learns**, in `run.log`: the file's firmware name, version
  and whole table, the version the device reports, and whether the flash matched.
  The fetched file stays out of this repository: `images.py` pins its name and hash.

To try one in the emulator, make its whole flash and start a plug from it:

```sh
python3 tools/beken/rebuild_dump.py S61-DUMP.bin OFFICIAL.bin captures/device.bin
AUTOFLASH_BEKEN_DUMP=captures/device.bin tools/beken/emulator/bk-emu.sh plug
```

This proves the firmware and the tool's path, not the device's hardware. The
bootloader, radio data and settings are the S61's, and the stock firmware's model
comes from its sealed factory data, so it says it is an S61 (`SN-BK7238-S61-01`).
The stub gives the model the app itself holds instead (`cloud_stub.py`). Whether a
real one reports that model is not known: if not, B1 stops with nothing written and
the model in the log. The emulator has no relay, button or LED, so it says nothing of
the pin map.

### The candidates, and why only one was added

All are BK7238 on the 4 MB table, and each file's stock firmware comes online on the
stub. A device stays out without a pin map to trust: pins are never guessed.

| Device | Firmware name, stock model | Pins | Added |
|---|---|---|---|
| Basic Gen5 `BASIC-1GS` | `FWSW-01-BASICR5-BK7238`, `SN-BK7238-BASICR5-01` | [one person's template](https://www.elektroda.com/rtvforum/topic4167932.html), in OpenBeken's device list | yes |
| DIN rail `BASIC-1GSP` | `FWSW-01-BASIC1GSP-BK7238`, `SN-BK7238-BASIC1GSP-01` | none published. It has a meter, and forum questions describe a latching relay driven by pulses, which a wrong map could harm | no |
| `S41STPB` (S41s, US) | `FWSW-01-S41-BK7238`, `SN-BK7238-S41-01` | none published. Built from the S61's source tree (`BK7238_S61_S41`), which does not make its pins the S61's | no |
| T6 `T6-1C/2C/3C-120M` (TX Gen2) | `FWSW-06-SWITCHES-BK7238`, `SN-BK7238-T6C3-01` | [one two-pad unit's](https://github.com/saltech1/esphome-sonoff-t6-uart): relays P6 and P26, LED P9. No third relay is known, and its touch pads talk over a UART protocol (P1), which OpenBeken has no driver for | no |

The 2 MB devices, the door sensor among them, are on hold.

## Status

The addresses here are S61 1.0.2's.

- **Root cause:** the app layer never started because the `app` task
  hangs in a flash-UID factory integrity self-check (`bk_flash_get_uid` hashed vs a
  stored cert; the emulator's synthetic UID can't match). `integrity_gate.py`
  clears it and the firmware boots the app framework and **registers the button by
  itself** (pins 0 and 2).
- **Key-driver timer thread wake (found + handled):** the periodic that times the
  button hold runs on the `bk_timer` hardware timer = **Timer 5**; the emulator
  raised only Timer 3 (the RTOS tick), so the thread (`0x3eba0`) never woke.
  `long_press.py` raises Timer 5's status and the thread wakes natively, the
  scan (`0x421e0`) runs, and the long-press action `0x42250` fires (it drives the
  relay/LED via `bk_gpio_output`). A blanket raise in the emulator's `_pacing`
  regresses 3 other BK7238 self-tests, so it is scoped to the tool; a clean fork
  fix needs per-timer-period modelling.
- **Pairing mode reached:** the appfw state machine (`ais_appfw_task`)
  is alive and cycling, waiting on `rt_event_recv` (`0x134da8`, event `[0x4040e0]`).
  Calling the firmware's pairing-method dispatcher `0x21eb4` from that task's
  context (`pair_drive.py`) brings the emulated radio up: **method 0 beacons as
  a soft-AP** (the peer sees an access point), **method 1 enters station mode** and
  associates. So pairing mode is observably entered.
- **On a network, the faithful way:** `seed_join.py` seeds
  `wifi-ssid`/`wifi-pass` at the firmware's own `ef_get_env_blob` and the plug
  connects for real — associate → WPA2 four-way → **IPv4 DHCP → 192.168.1.100**.
  This corrects the main doc's old "seeding leaves it idle" note (that was the
  integrity gate). `ble_provision.py` reaches the same Wi-Fi join via the real
  eWeLink BLE provisioning messages, driven through the firmware's dispatcher.
- **Cloud dial achieved:** `cloud_dial.py` gets the plug to dial
  the **eWeLink cloud**. Left alone after the join the plug does **Matter**
  (`_matterc`); the eWeLink cloud link is created only on pairing success. That
  trigger is one firmware call (the start-cloud handler `0x2c204`, whose only
  callers are in the pairing state machine) — synthesising it, with the AIS cloud
  context already factory-populated (UUID/model/MAC/dispatch-URL at `*0x4044ac`),
  makes `ais_link_task` send the CoolKit **`GET /dispatch/device`** over TCP,
  steerable at a local stub. A genuinely bound dump is still useful only for a
  *faithful* server + device-secret. See the main doc for the full gate chain.
- **OTA / conversion path mapped:** the S61 flashes updates the same
  CoolKit way as the S60 — an `upgrade` command names a `downloadUrl` the sender
  controls.
  The cloud download is a CoolKit **FOTA** container (magic `0x1beef11e`, HTTP 206,
  version gate, CRC32s); the bootloader then applies an `rt_ota` **RBL**. Tested by
  staging an RBL directly in the download partition and booting: the bootloader
  **erases + writes a third-party unsigned image to the app** — **no signature
  wall** (`rt_ota_custom_verify` is a link-address check, not crypto). The apply path
  is **LZMA decompression + standard CRC-32** (with an FNV-1a `raw_hash`), all in
  software, with **no payload decryption** — the `crypt` field is a header flag, not a
  cipher, and the download path writes bytes verbatim too. So the whole OTA chain has
  **no signature, no encryption and no secret key**. **Proven with a real image:** OpenBeken ships BK7238 `OpenBK7238_<ver>.rbl` OTA images; staged on
  the stock bootloader (no bypasses) it decompresses + writes the full image, passes
  every firmware gate including the final hash verify, and **boots OpenBeken** — the
  emulator performs the whole apply itself (once it models the flash JEDEC id + XIP
  write-through; see the main doc). The full image also **stages verbatim over the
  cloud path** (byte-for-byte). `fota_wrap.py` wraps any `.rbl` in the FOTA
  container (and can serve it).
  (It is not a hardware-AES engine with a baked key, as an earlier version of this
  page said.) See
  [the main doc](../../docs/beken-s61-emulation.md#firmware-update-and-conversion-the-ota-path).
- **Cloud ONLINE achieved:** `cloud_online.py` completes the
  handshake against a local stub — dispatch → TLS `/api/ws` → `register` → ONLINE,
  then heartbeats. Two S61-specific pieces: the dispatch reply must be compact JSON
  with a `domain` key and a string `port`; and the `wss://` IoT cert is verified,
  cleared by the S60's honest trust-anchor swap (`--trust-ca`) applied at
  `mbedtls_x509_crt_parse` (`0x125e4c`), since the S61 builds its CoolKit CA in RAM
  rather than storing it in flash. No verification is weakened — only the trusted
  root is swapped for the tool's own, and the stub serves a leaf signed by it.
- **AirKiss — decoder stubbed in this build:** frame delivery into
  the MAC is solved (100 %, `sent == rx` on a synthetic self-test and a real
  7446-frame capture) once injection is gated on the RX-FIQ handler object
  (`S61_RX_OBJ_PTR`, default `0x403c10`); the tool sets `monitor_rx_deliver` on the
  fork's MAC. But there is **no decoder to reach**: `0x403b20` is the monitor-frame
  callback (RX path reads it and `blx`es it per frame), and the only value the
  firmware ever registers is the stub `0x12c483` (`bx lr`). Setting it and replaying
  11 000+ frames calls the stub once per frame, 0 faults, nothing decoded. eWeLink
  kept the AirKiss scaffolding but compiled out the EZ decoder; real pairing is
  soft-AP + BLE. The delivery/replay tooling is retained for firmware that *does*
  decode.
- A capture carries the credentials of whatever network it was taken on, in its
  frame lengths, so a `.pcap`/`.pcapng` is **never committed** — the leak scan
  refuses one by its name. A regression fixture uses synthetic frames instead
  (`airkiss_replay.py --selftest`), not a captured file.
