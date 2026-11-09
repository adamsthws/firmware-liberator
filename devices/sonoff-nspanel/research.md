# Sonoff NSPanel: research

What was found out about the NSPanel's own firmware. What it shares with the other
Espressif devices is in [espressif-research.md](../../docs/espressif-research.md).

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
