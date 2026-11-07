# Sonoff S61: research

What is known of the S61's own hardware, and what OpenBeken was seen to do on it in
the emulator. Its firmware is the one the Beken research was done on:
[beken-research.md](../../docs/beken-research.md).

## Energy-meter calibration: the stock formula, and the OpenBeken mapping

The meter is a **BL0937** (the stock driver is registered as `bl0937b`). Its
factory calibration is the EF40 key **`elecCalibCoef`** (80 B): `u32` magic, a
flag byte (`1`), padding, then **three 24-byte records in the order current,
voltage, power**, each beginning with three `int32` **`a, b, c`** (the rest of
each record is unused). The setter at `0x31160` writes that shape; the getter at
`0x311ac` checks magic and flag and hands the driver init (`0x301c0`) the 72-byte
payload, which copies each record's `a,b,c` into the driver's calibration object
(`0x404bbc`, mode 1).

The driver's measurement function (`0x41a84`; `0x41918` is the uncalibrated
twin) works in doubles. From the pulse period it forms the frequency `f` (Hz) and
the **HLW8012-datasheet nominal value** with these board constants:

| quantity | nominal from `f` | constants |
|---|---|---|
| current | `I = f · 1.1 · 1000 / 94638` | 94638 (datasheet), 1 mΩ shunt |
| voltage | `V = f · 1.1 · 2040 / 15397` | 15397 (datasheet), 2040:1 divider |
| power   | `P = f · 1.21 · 2040 · 1000 / 1721506` | 1721506 (datasheet), 1.21 = 1.1² |

(1.1 plays the role of V_ref; BL0937's nominal is 1.218 V, which is why the
factory trims below come out ~0.88–0.95.) It then applies the factory trim

```
x_cal = a·x²/1e7 + b·x/1e6 + c/1e6
```

— the same CoolKit quadratic mvoss96 found in the S60's `calib_coefficient0`
(adamsthws/firmware-liberator PR #7). Currents below 3 mA zero current and power. On the
dump: `a = c = 0` for all three, `b = 954547 / 879893 / 930747` (I/V/P): a pure
linear trim.

**OpenBeken's BL0937 driver measures the same `f`** — it counts falling edges of
CF/CF1 per second (`drv_bl0937.c`) and scales `real = f · cal`
(`drv_pwrCal.c`, `PWR_CAL_MULTIPLY`). So for a linear trim the mapping is exact:

```
cal_I = (1.1·1000/94638)        · b_I/1e6   = 0.011623 · 0.954547 = 0.011095
cal_V = (1.1·2040/15397)        · b_V/1e6   = 0.145743 · 0.879893 = 0.128238
cal_P = (1.21·2040·1000/1721506)· b_P/1e6   = 1.433861 · 0.930747 = 1.334562
```

(OpenBeken's generic defaults are 0.0118577 / 0.1325301 / 1.5 — same magnitudes,
which is the independent check that `f` means the same thing in both.) The
import module applies these with `CFG_SetPowerMeasurementCalibrationFloat` on
first boot, and sets `PowerMax` to the plug's 3840 W rating. A non-zero `a` or
`c` has no OpenBeken equivalent; the module then uses `b` and logs a warning.
Not yet done: a check against a real metered load on hardware.

## The S61's wiring (proven OpenBeken template)

The OpenBeken device database carries an entry for exactly this board,
**S61STPF / `WBBK_01P_V1.3`**, verified on hardware by its author
(elektroda.com topic 4157852; `OpenBekenIOT/webapp` `devices.json`):

| pin | role | channel |
|---|---|---|
| P26 | `Rel` (relay) | 1 |
| P9 | `Btn_Tgl_All` (button) | 1 |
| P0 | `LED_n` (relay/power LED, active-low) | 1 |
| P1 | `WifiLED_n` (Wi-Fi LED, active-low) | 2 |
| P8 | `BL0937CF` (power pulses) | – |
| P6 | `BL0937CF1` (V/I pulses) | – |
| P24 | `BL0937SEL` | – |

It agrees with the stock driver: the 6-byte parameter block its init passes to
`bl0937b::open()` (`[0x13548c]+0x24` = `26 00 00 00 02 18`) ends in `0x18` =
P24 for SEL (and `0x26` = `8<<2 | 6` looks like CF/CF1 packed). The template's
`loglevel 0` worked around UART logging disturbing pulse counting on BK7238,
fixed upstream in 1.18.235 (PR #1930); the release this project builds on has
the fix. The import
module applies this template on first boot, plus `SetStartValue 1 -1` so the
relay restores its last state after a power cut (what the tool sets on the S60).
The ESPHome config ([`devices/sonoff-s61/esphome.yaml`](esphome.yaml),
on LibreTiny) uses the same pins (`hlw8012: model: BL0937, sel_pin: P24,
cf_pin: P8, cf1_pin: P6`); see [the ESPHome route](../../docs/beken-research.md#the-esphome-route-libretiny).

## OpenBeken on the S61: what the emulator showed

The S61's table for [the tool's OpenBeken driver](../../tools/beken/openbeken/README.md)
is [`drv_autoflash_sonoff_s61.c`](drv_autoflash_sonoff_s61.c): its kind and stock
model, where its stock store (`0x3e0000`-`0x3ec000`), download slot (`0x286000`) and
OpenBeken's settings (`0x3e1000`) sit, its wiring ([above](#the-s61s-wiring-proven-openbeken-template)),
and its BL0937 meter's calibration ([above](#energy-meter-calibration-the-stock-formula-and-the-openbeken-mapping)):
how its `elecCalibCoef` record reads, the datasheet constants, the 3840 W rating.

The driver, then still the S61's own import module, was built and booted in the
BekenEmulator on a 4 MB flash shaped like a converted plug (OpenBeken app + the
stock EF40 at `0x3e0000`, provisioned with a test SSID). On first boot:

```
Info:CFG:S61Import: imported stock Wi-Fi SSID 'MyTestNet' (11-char pass)
Info:MAIN:Using SSID [MyTestNet]
[WIFI] ...completed the four-way handshake; the link is encrypted
[NET]  DHCP: device ... got 192.168.1.100
```

The module read the stock creds and OpenBeken joined - no typing, no offline
config surgery.

With the full module (4 MB-table build) converted over the stock bootloader:

```
[I/OTA] OTA firmware(app) upgrade(1.0.2->s61v6) startup.
S61Import: imported stock Wi-Fi SSID 'MyTestNet' (11-char pass)
S61Import: stock BL0937 factory trim b=954547/879893/930747 (I/V/P) -> OpenBeken cal I=0.011095 V=0.128238 P=1.334562, PowerMax 3840 W
S61Import: applied the S61 pin template (Rel P26, Btn P9, LED P0, WifiLED P1, BL0937 CF P8 CF1 P6 SEL P24); relay restores its last state
Info:MAIN:Started BL0937.
```

and the config OpenBeken then saved at `0x3e1000` decodes to: name `S61` (the module's name then; now `openbeken-abcdef`); P26
`IOR_Relay` ch 1, P9 `IOR_Button_ToggleAll` ch 1, P0 `IOR_LED_n` ch 1, P1
`IOR_LED_WIFI_n` ch 2, P8 `IOR_BL0937_CF`, P6 `IOR_BL0937_CF1`, P24
`IOR_BL0937_SEL`; `startChannelValues[1] = -1`; cal `V=0.128238 I=0.011095
P=1.334562 PowerMax=3840`. On a 2 MB-table build `S61RevertToStock` (now
`AutoflashRevertToStock`) refuses
(`OTA slot is 0x132000, not the stock download slot 0x286000 - refusing`), and
on the first 4 MB-table build without the `hal_ota.h` patch the downloader still
wrote to `0x132000` and the stock bootloader rebooted straight back into
OpenBeken - which is why the command checks both addresses.

It was also validated through the **full OTA conversion**: the `.rbl`, staged in
the stock download partition (`0x286000`), is applied by the stock `rt_ota`
bootloader (`OTA firmware(app) upgrade(1.0.2->s61import)` → `partition 'app' erase
success`), OpenBeken boots, and the module imports from the untouched EF40 and
joins.

The two upstream patches were checked on a clean clone. The default 2 MB build is
unchanged: config at `0x1e1000`, a REST read beyond 2 MB refused. The
`FLASH_SIZE=4M` QIO boots in the emulator on a 4 MB flash, saves its config at
`0x3e1000`, answers `GET /api/flash/3e0000-1000`, and `ota_http` logs `init OTA,
startaddr 0x286000`, where its own 4 MB bootloader applies the download on
reboot: `upgrade(4M.1220->s61v6)` → `_STREAM_END` → slot erased. The emulator
then stays at the bootloader banner; the flash it persisted is right - the
written app verifies against its RBL header's FNV-1a hash and, loaded afresh,
boots s61v6 and joins - so that is emulator state after an OpenBeken-initiated
watchdog reboot, not the image: the same RBL applied from a bootloader-only boot
of the same flash comes up first time.

Reverting was verified in the emulator both ways. Staged by hand on a converted
plug, the bootloader logs `upgrade(s61v2->1.0.2)`, rewrites `app`, and the stock
firmware boots with the EasyFlash store intact. And end to end through OpenBeken
itself - the stock RBL served from the host, `S61RevertToStock <url>` sent over
HTTP - the log reads `init OTA, startaddr 0x286000` → `Rebooting in 1 seconds` →
`OTA firmware(app) upgrade(s61v6->1.0.2) startup` → `The partition 'app' erase
success` → `EasyFlash V4.1.99 is initialize success`, the same 33 EF40 keys
still there afterwards.
