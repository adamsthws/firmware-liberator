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

What each file takes, in `vars:`, is at its top. The S60's config shows them
all in use:

```yaml
packages:
  base: !include ../shared-esphome/base.yaml
  wifi: !include ../shared-esphome/wifi-esp32.yaml
  relay: !include {file: ../shared-esphome/relay.yaml, vars: {relay: relay, name: "Relay", default: "true", countdown: "Countdown"}}
  energy: !include {file: ../shared-esphome/energy-totals.yaml, vars: {energy: energy, id_end: "", name_end: ""}}
  overload: !include {file: ../shared-esphome/overload.yaml, vars: {relay: relay, power: power, id_end: "", name_end: "", max: "4000", default: "3500"}}
```

A device with two channels, as the Dual R3, includes a meter's files once for
each, with `id_end: "_1"` and `name_end: " 1"`, then `"_2"` and `" 2"`. A device a
file does not apply to says why beside its includes, as the POWCT's does for
overload: its clamp measures a circuit of its own, not the relay's load.

Home Assistant fetches this folder with a device's config when it adopts the
device, as the config's `../shared-esphome/` names it. If you keep a copy of a
device's config yourself, keep a copy of this folder with it
([how](../../docs/home-assistant-adoption.md#using-your-own-config-instead)).

`tests/repo/test_esphome_shared.py` holds every device's config to this: each
includes what applies to it, and none keeps a copy of its own of what is here.
