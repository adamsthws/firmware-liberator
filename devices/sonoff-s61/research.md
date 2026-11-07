# Sonoff S61: research

What is known of the S61's own hardware. Its firmware is the one the Beken research
was done on: [beken-research.md](../../docs/beken-research.md).

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
