# Adding a device

How to add an Espressif device to the tool, and prove it in the emulator before
it goes near a real one. Beken devices are at the end: they cannot yet be added
this way.

## What you need

- **A whole-flash dump of the device on stock firmware, if you can find one.**
  [FlashDumps](https://github.com/openshwprojects/FlashDumps) has several. A dump
  has the device's real partition table, settings and factory data, so what you
  test is what the device has. An owner's [readout](readouts.md) is one.
- **Failing that, eWeLink's public update file for it.** It runs on another
  plug's flash (a *donor*), which tests the firmware but not the device's own
  layout. Some firmware will not get far this way: see
  [When it does not boot](#when-it-does-not-boot).
- **Its wiring**, as a Tasmota template and an ESPHome config. Blakadder's
  template list and ESPHome's device list are the usual sources.
- **Not locked.** Secure boot or flash encryption rules a device out: a dump that
  looks encrypted, or eWeLink's newer FOTA container for ESP firmware, is the
  sign. Matter versions of a device are usually locked.

Everything public you fetch stays out of the repository: no Sonoff firmware or
dump is ever committed. Tooling fetches it at run time.

## The steps

1. **Add a test copy.** Add the device to `DUMPS` or `UPDATES` in
   [public_dump.py](../tools/espressif/emulator/public_dump.py), with the file's
   hash and an unused number, then make it:

   ```sh
   .venv/bin/python tools/espressif/emulator/public_dump.py sonoff-example
   ```

   This replaces the device's identity with a made-up one under
   `captures/plugs/`. For an update file, use the model its firmware calls itself
   (`strings` finds it, in the shape `SN-...`, `E32-...` or `ESP32D0-...`).

2. **Add the kind** to [kinds.py](../tools/autoflash/kinds.py): its chip, eWeLink
   product model, stock model, the model its update wrapper names, and its Tasmota
   template. Set `product_model_decides` if another device shares its stock model
   but is wired differently, so it is never mistaken for that one. Give it a
   status in `devices/status.json`: `Not ready yet` until step 6 passes
   ([its status](#its-status)).

3. **Boot it in the emulator** with the stub cloud, and watch for it to come
   online (step 6 runs this and shows what it logged):

   ```sh
   tools/espressif/emulator/emu.sh --stub-cloud --fresh --plug 12:34:56:e3:2d:NN
   ```

4. **Add its partition layout**, if new, to
   [layouts.py](../tools/espressif/layouts.py). A dump's table is in its flash at
   `0x8000`. The tool refuses a real device whose table it does not know, before
   overwriting anything, so an unknown layout stops safely. To leave Tasmota, the
   mover goes at `0x20000`. If your device's `ota_0` is elsewhere, as the TH Elite's
   is, give the layout a `temporary_sha256`: its temporary table is built at run
   time, with `ota_0` at `0x20000`, and `tests/espressif/test_layouts.py` holds it
   to that hash. Its `ota_1` has to be where the device's own table has it.

5. **Add its firmware.** Write `devices/<key>/esphome.yaml`, `esphome.md` and
   `README.md`, the last starting with its status, and its table with a
   `| Firmware name |` row, after its chip, of the kind's `wrapper_model`
   (`tests/repo/test_firmware_names.py` holds the two alike). Lay both pages out
   as another device's of its chip family are: `tests/repo/test_device_pages.py`
   holds the sections, table rows and bullets they share. Then build and pin
   the image. `artifacts/` changes only this way:

   ```sh
   .venv/bin/python tools/repo/update_firmware.py esphome --plug sonoff-example
   ```

6. **Convert it both ways**, each from a fresh copy:

   ```sh
   tools/espressif/emulator/unattended-test.sh --stub-cloud --fresh \
       --plug=12:34:56:e3:2d:NN --save=example-tas -- --firmware tasmota
   tools/espressif/emulator/unattended-test.sh --stub-cloud --fresh \
       --plug=12:34:56:e3:2d:NN --save=example-esp -- --firmware esphome
   ```

   Then go back to Sonoff from each:

   ```sh
   tools/espressif/emulator/unattended-test.sh --stub-cloud \
       --plug=12:34:56:e3:2d:NN --from=example-tas -- --firmware sonoff
   ```

7. **Run the tests**, and add the ones the device needs: `tests/autoflash/test_kinds.py`
   covers telling it apart from its neighbours.

   Nothing needs wiring in for updates: the weekly update builds every device's
   ESPHome with one release and moves each chip's Tasmota with the ESP32-C3's
   (`update_firmware.py`'s `esphome_command`). A test fails if it cannot tell how
   to build a device's, or if its chip has no Tasmota that follows.

   A device the tool converts has its own run of the `emulator-check` workflow, from
   its public dump: each final firmware, then each plug taken to the other
   ([emulator_check.py](../tools/repo/emulator_check.py)). One with no dump is named
   in that file's `NO_DUMP`, or a test fails.

8. **If it will not convert yet**, keep the work: put its folder in
   [devices/not-ready-yet/](../devices/not-ready-yet/README.md), with its status
   `Not ready yet`, and say there what is holding it up. The tool then turns it down.

9. **Be plain about what was tested.** The emulator has no relays, meters, buttons
   or screens. A conversion passing there says the firmware installs and starts,
   not that the template is right.

10. **Write down what you found out**, so the next person need not repeat it. What
    is true of this one device goes in a `research.md` in its folder, listed in
    [device-research.md](device-research.md); a test fails until it is. What holds
    for its whole chip family goes in [espressif-research.md](espressif-research.md)
    or [beken-research.md](beken-research.md).

## Its status

Each device has one status, in [`devices/status.json`](../devices/status.json), and
nowhere else:

| Status | Means | The tool |
|---|---|---|
| `Proven` | a real one has been converted and reported | converts it |
| `Testers wanted` | it converts in the emulator, or is added from its official file and a published pin map; no real one is reported yet | says so in the go-ahead before the first write, and asks for the run's log |
| `Not ready yet` | it does not convert in the emulator yet | turns it down |

After changing the file, run `python3 tools/repo/device_status.py`: it writes each
device's badge file under `devices/status/`, with the words and colour of its status.
The badges read those from GitHub, so they show what is on `main` once it is pushed,
not your local edits. The warning before a first write is the same for every device
wanting testers (`tools/autoflash/testers.py`).

A device's README says its status only through its badge, at the top of its
`## Status` section. The main README lists it in its table with the same badge.
`tests/repo/test_device_status.py` fails if any of these disagree with the file,
or if a README says a status in words.

**When a report comes in** of a real one converted, change its line in
`devices/status.json` to `Proven`, and run `tools/repo/device_status.py`. That moves
the tool and every badge.

## What the emulator does for you

No device needs its own patch for these. Each is found in the firmware, not listed
by address.

| What | Why | Where |
|---|---|---|
| Bluetooth start-up skipped | QEMU has no Bluetooth; the functions calling `esp_bt_controller_init` return at once | `patch_stock.py` |
| DPORT stall skipped (classic ESP32, ESP-IDF 4) | a workaround for a silicon bug QEMU does not have; the other core can be too slow to answer | `patch_stock.py` |
| eWeLink's CA replaced with the stub's | the stub has to be trusted. RSA where the firmware's is RSA, or its stack overflows checking an EC signature | `patch_stock.py`, `make_ca.py` |
| Wi-Fi and cloud settings rewritten | points the plug at the emulator's network and stub | `rewrite_nvs.py` |
| Chip revision (ESP32-C3) | taken from the app's header, or its bootloader refuses to start it | `public_dump.py` |

Set `psram_mb` on the kind if its stock firmware stops at start-up without its
external RAM. Run the emulated plug with up to one emulator per core: more gives
false failures.

## What a run tells you

Every backup logs what it found, in the run's `run.log`, and on the screen when the
partition table is not one the tool knows: the chip and its revision, whether
secure boot and flash encryption are on, the flash size, the partition table, and
each app's version, ESP-IDF and the firmware name its updater checks. The installer
adds what a backup cannot hold, such as the exact chip revision, the flash chip's id
and the input pins' levels ([what it logs](espressif-research.md#what-a-backup-logs)).
None of it is secret, so a report of a device the tool stopped on is enough to start adding it.

## Learning the firmware name

A device's stock updater refuses a file wrapped for another firmware name, and the
names vary too much to guess: `FWSW-E32S60-S60-ESP32C3FN4`,
`FWSWES-E32DR3-DUALR3-ESP32D0WDV3`, `FWPNL-E32NSP-NSPANEL-ESP32D0WDV3`. A wrong name
does no harm, as the device reports a failed update and stays on Sonoff's firmware.

Before anything is written, each run tries to learn the name and logs it in
`run.log`, with the chip it implies. This also happens on a device the tool then
turns down, so a report of one already has its name.

1. **The file eWeLink offers.** eWeLink offers nothing to a device on the latest
   firmware, so the tool also asks with the device's own id and version `0.0.1`,
   which gets it the latest. It then reads the first bytes of that `user1.bin`, not
   the rest of the file, and never logs the link or the id. This changes nothing on
   the device: it is only asked of eWeLink.
2. **The public file list.** If that gives no name, the tool looks in the filenames
   of the
   [public update files](https://github.com/openshwprojects/FlashDumps/tree/4d3556605da2ce101382a01b6fb7d84e68c3df2a/IoT/zOTAz/eWeLink),
   matching eWeLink's product model, then the model the stock firmware names. It
   gives no name unless exactly one fits.

What has been tried on eWeLink itself, on one S60:

- The server goes by the device's id and ignores the model, so you cannot look up a
  device you do not hold. A made-up id gets an unrelated file, so never use one,
  and an empty id gets an error.
- The download link answers an empty 400 as eWeLink gives it. With
  `?deviceid=<the device's id>&ts=1&sign=0` added it answers, and a range request
  gets just those bytes. The signature is not checked.

The S60's name read this way matched its `wrapper_model`. Other devices, including
Beken ones, have not been tried this way. The Beken route uses only the public list.

For a kind the tool knows, a name that differs from its `wrapper_model` is shown on
the screen as well, and the conversion goes on as before. A run that cannot learn
the name goes on without it. A run on the cloud stub learns nothing, as the stub
offers no file and the run does not look further.

To use the name in your own code, call `firmware_name.learn()` in
[firmware_name.py](../tools/ewelink/firmware_name.py). It returns the name, its chip
and where it came from.

## When it does not boot

Read `captures/emulation/state/logs/uart.log` (`state-N` with `--instance=N`).

| You see | It means | Do |
|---|---|---|
| `abort()` straight after `Disabling RNG`, no other message | external RAM missing | set `psram_mb` |
| `This chip is revision 0 but ...` | eFuses too old | make the copy again: `public_dump.py` sets it |
| `A stack overflow in task colink task` | stub CA is a heavier type than the firmware's | see the CA row above |
| `Interrupt wdt timeout` at start-up | a core waiting for the other | look for a new cross-core wait; do not stretch the watchdog |
| `factory mode`, `factory pid not set` | factory data only a real unit has | a whole dump is needed |
| Both CPUs idle after `factory?`, no Wi-Fi | it is waiting for something the donor lacks | a whole dump is needed |
| `ESP_ERR_NOT_FOUND` from `espInit` | a partition or setting the donor lacks | a whole dump is needed |

## Beken devices

The Beken emulator's cloud bring-up (`tools/beken/emulator/cloud_online.py`,
`cloud_dial.py`) finds the places it needs in the firmware itself
(`tools/beken/stock_points.py`), and needs a whole flash to run from. To see
whether a device's firmware can come up this way, run `stock_points.py` on its
official file: each point it cannot find is named. The app of a BK7238 official
file runs on another plug's bootloader and settings (`rebuild_dump.py`), but only
the device's own whole flash has its factory data.

A 4 MB BK7238 device with no whole dump can be added from its official file and a
pin map you can trust, with the status "Testers wanted" and its warning:
[how, and what that proves](../tools/beken/README.md#adding-a-4-mb-bk7238-device-with-no-whole-dump).
