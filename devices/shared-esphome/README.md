# What every device's ESPHome shares

Each device's `esphome.yaml` holds only what is its own: its pins, its meter,
its buttons, its display. Everything a device has because it is a device of
this project, or because it has a relay or a meter, is here, and each config
includes it under `packages:`. A change here reaches every device at once, and
a device added later gets all of it by including the same files.

| File | What it gives | Included |
|---|---|---|
| [`base.yaml`](base.yaml) | The API (with `autoflash_wifi`), updates, the web page, Wi-Fi and the device's own network, the clock, the `WiFi Signal` sensor, and the network it started on saved so that it stays | by every device |
| [`wifi-esp32.yaml`](wifi-esp32.yaml) | The network to start on, from the placeholders the conversion tool writes into the image | by every Espressif device |
| [`wifi-bk72xx.yaml`](wifi-bk72xx.yaml) | The network to start on, from the stock firmware's store, read with [`ef40.h`](ef40.h) | by every Beken device |
| [`relay.yaml`](relay.yaml) | A relay's power-on state (`Off`, `On`, `Last state`), its state written to flash as it switches, and its countdown | once for each relay |
| [`energy-totals.yaml`](energy-totals.yaml) | A meter's daily, monthly, yearly and total energy, kept through restarts and updates | once for each meter channel |
| [`overload.yaml`](overload.yaml) | The relay switched off when its load stays over a threshold the owner sets | once for each meter that measures its relay's own load |
| [`derived-power.yaml`](derived-power.yaml) | A meter's apparent power, reactive power and power factor, worked out each second from its voltage, current and power | once for each meter channel |

What each file takes, in `vars:`, is at its top. The S60's config shows them
all in use:

```yaml
packages:
  base: !include ../shared-esphome/base.yaml
  wifi: !include ../shared-esphome/wifi-esp32.yaml
  relay: !include {file: ../shared-esphome/relay.yaml, vars: {relay: relay, name: "Relay", default: "true", countdown: "Countdown"}}
  energy: !include {file: ../shared-esphome/energy-totals.yaml, vars: {energy: energy, id_end: "", name_end: ""}}
  overload: !include {file: ../shared-esphome/overload.yaml, vars: {relay: relay, power: power, id_end: "", name_end: "", max: "4000", default: "3500"}}
  derived: !include {file: ../shared-esphome/derived-power.yaml, vars: {voltage: voltage, current: current, power: power, id_end: "", name_end: ""}}
```

A meter whose voltage, current and power are each averaged over a second, as a
CSE7766's are, hands each of its readings to `derived-power.yaml` before they
are averaged, as the S60's and POW Elite's do: its voltage's and power's
filters set `id(derived_frame)[0]` and `[1]`, and its current's, the meter's
last, runs `id(derived_reading).execute(i)`. ESPHome starts each average at a
moment of its own, and worked out from the three averages, a load that changes
within the second would read as reactive. Any other meter's last readings are
taken as they are.

A device with two channels, as the Dual R3, includes a meter's files once for
each, with `id_end: "_1"` and `name_end: " 1"`, then `"_2"` and `" 2"`. A device a
file does not apply to says why beside its includes, as the POWCT's does for
overload: its clamp measures a circuit of its own, not the relay's load. Its
derived readings are of that circuit, as its power is, so it has those.

Home Assistant fetches this folder with a device's config when it adopts the
device, as the config's `../shared-esphome/` names it. If you keep a copy of a
device's config yourself, keep a copy of this folder with it
([how](../../docs/home-assistant-adoption.md#using-your-own-config-instead)).

`tests/repo/test_esphome_shared.py` holds every device's config to this: each
includes what applies to it, and none keeps a copy of its own of what is here.
