# Firmware Liberator
**Sonoff eWeLink → Tasmota, OpenBeken, ESPHome**
> **Firmware Flasher Tool**  
(Over The Air - Flash over Wi-Fi - 'OTA')

<br>

[![Runs on Linux, macOS and Windows](https://img.shields.io/badge/Runs%20on-Linux%20%7C%20macOS%20%7C%20Windows-lightgrey)](#what-you-need)

[![Installs ESPHome](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fartifacts%2Ffirmware.json&query=%24.esphome.image.esphome&label=Installs%20ESPHome&color=blue)](#what-you-end-up-with) [![Installs Tasmota](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fartifacts%2Ffirmware.json&query=%24.tasmota.version&label=Installs%20Tasmota&color=blue)](#what-you-end-up-with) [![Installs OpenBeken](https://img.shields.io/badge/dynamic/json?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fartifacts%2Ffirmware.json&query=%24.beken%5B%22sonoff-s61%22%5D.openbeken.release&label=Installs%20OpenBeken&color=blue)](#what-you-end-up-with)

[![Release](https://img.shields.io/github/v/release/adamsthws/firmware-liberator?label=Release)](https://github.com/adamsthws/firmware-liberator/releases/latest) [![Tests](https://img.shields.io/github/actions/workflow/status/adamsthws/firmware-liberator/emulator-check.yml?branch=main&label=Tests)](https://github.com/adamsthws/firmware-liberator/actions/workflows/emulator-check.yml) [![License: GPL-3.0](https://img.shields.io/github/license/adamsthws/firmware-liberator?label=License)](LICENSE)

[![Discussions](https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fimg.shields.io%2Fgithub%2Fdiscussions%2Fadamsthws%2Ffirmware-liberator.json&search=(%5Cd%2B)%20total&replace=%241&label=Discussions&color=blue)](https://github.com/adamsthws/firmware-liberator/discussions) [![Issues](https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fimg.shields.io%2Fgithub%2Fissues%2Fadamsthws%2Ffirmware-liberator.json&search=(%5Cd%2B)%20open&replace=%241&label=Issues&color=blue)](https://github.com/adamsthws/firmware-liberator/issues) [![Pull Requests](https://img.shields.io/badge/dynamic/regex?url=https%3A%2F%2Fimg.shields.io%2Fgithub%2Fissues-pr%2Fadamsthws%2Ffirmware-liberator.json&search=(%5Cd%2B)%20open&replace=%241&label=Pull%20Requests&color=blue)](https://github.com/adamsthws/firmware-liberator/pulls) [![Downloads](https://img.shields.io/github/downloads/adamsthws/firmware-liberator/total?label=Downloads&color=blue)](https://github.com/adamsthws/firmware-liberator/releases)

<br>

## Keep this tool alive and growing  
Your kind donation goes toward expansion, supporting many more devices - Thankyou!  


[![GitHub](https://img.shields.io/badge/GitHub-Support-EA4AAA?logo=githubsponsors&logoColor=white)](https://github.com/sponsors/adamsthws) [![Ko-fi](https://img.shields.io/badge/Ko--fi-Support-FF5E5B?logo=ko-fi&logoColor=white)](https://ko-fi.com/adamsthws) [![PayPal](https://img.shields.io/badge/PayPal-Support-00457C?logo=paypal&logoColor=white)](https://www.paypal.me/adamsthws)  

> Bonus: Sponsor through GitHub and you earn the coveted Sponsor achievement on your profile: <img src="docs/PublicSponsor.png" alt="Public Sponsor achievement" height="28" align="absmiddle">

<br>

### Liberation From The Cloud

Your device answers to you alone, on your own network.
> Removes dependence on external cloud servers. After flashing you'll no longer require a cloud account or third party app - your device will be truely local and fully under your control.

<br>

### Fully automated 'Over The Air' firmware conversion

Turn your Sonoff device into one running **ESPHome**, **Tasmota** or **OpenBeken**, using OTA method - no soldering, no taking the device apart.

> Conversion with this tool is fully automated end to end, with checks at every stage. It is resumable and can be restarted at any point.

<br>

### Any Direction

And every route between them.
> Stock Sonoff to Tasmota, OpenBeken or ESPHome, either to the other, and back to Sonoff's own firmware if you change your mind.

<br>


![Sonoff (eWeLink), Tasmota or OpenBeken, and ESPHome: any one converts to any other](docs/FirmwareRoutes.svg)

<br>

### No More Soldering

Until now, the established way to flash a device was to open it, perform some soldering and flash it over USB-serial - hard work, because the case is often plastic-welded shut.

> The community threads for the soldering method: [S60, UK version](https://github.com/arendst/Tasmota/discussions/22554) & [S60, EU version](https://github.com/arendst/Tasmota/discussions/21255).

<br>

## What you need

**A supported device**
> See [Supported Devices](#supported-devices).

**An eWeLink account**
> A throwaway one is fine.  
> (The device has to be added to eWeLink first).

**A computer**
> Linux, macOS or Windows, with Wi-Fi.

**Admin rights**
> Needed to add one temporary firewall rule.  
> (Done automatically and removed when the tool finishes).

<br>

## Supported Devices
> More devices coming soon.

| Status | Device | Models |
|---|---|---|
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-s60.json)](devices/sonoff-s60/README.md) | [Sonoff S60](devices/sonoff-s60/README.md) | S60TPG (UK, Type G), S60TPF (EU, Type E/F) |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-s61.json)](devices/sonoff-s61/README.md) | [Sonoff S61](devices/sonoff-s61/README.md) | S61STPF (EU, Type E/F) |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-pow-elite.json)](devices/sonoff-pow-elite/README.md) | [Sonoff POW Elite 16A](devices/sonoff-pow-elite/README.md) | POWR316D |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-powct.json)](devices/sonoff-powct/README.md) | [Sonoff POW Ring](devices/sonoff-powct/README.md) | POWCT |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-th-elite.json)](devices/sonoff-th-elite/README.md) | [Sonoff TH Elite 20A](devices/sonoff-th-elite/README.md) | THR320D |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-dualr3.json)](devices/sonoff-dualr3/README.md) | [Sonoff Dual R3](devices/sonoff-dualr3/README.md) | DUALR3, DUALR3 Lite |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-basic-1gs.json)](devices/sonoff-basic-1gs/README.md) | [Sonoff Basic Gen5](devices/sonoff-basic-1gs/README.md) | BASIC-1GS |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-nspanel.json)](devices/not-ready-yet/sonoff-nspanel/README.md) | [Sonoff NSPanel](devices/not-ready-yet/sonoff-nspanel/README.md) | NSPanel |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-basicr4.json)](devices/not-ready-yet/sonoff-basicr4/README.md) | [Sonoff BASICR4](devices/not-ready-yet/sonoff-basicr4/README.md) | BASICR4 |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-minir4.json)](devices/not-ready-yet/sonoff-minir4/README.md) | [Sonoff MINI Extreme](devices/not-ready-yet/sonoff-minir4/README.md) | MINIR4 |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-m5-1c.json)](devices/not-ready-yet/sonoff-m5-1c/README.md) | [Sonoff M5 1-gang](devices/not-ready-yet/sonoff-m5-1c/README.md) | M5-1C |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-m5-2c.json)](devices/not-ready-yet/sonoff-m5-2c/README.md) | [Sonoff M5 2-gang](devices/not-ready-yet/sonoff-m5-2c/README.md) | M5-2C |
| [![Status](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Fadamsthws%2Ffirmware-liberator%2Fmain%2Fdevices%2Fstatus%2Fsonoff-m5-3c.json)](devices/not-ready-yet/sonoff-m5-3c/README.md) | [Sonoff M5 3-gang](devices/not-ready-yet/sonoff-m5-3c/README.md) | M5-3C |

> **Proven**: a real one has been converted and reported.
> **Testers wanted**: converts in the emulator, no real one reported yet. The tool warns you first, and asks for the run's log.
> **Not ready yet**: the tool does not convert it yet.

<br>

## Let's go

Simply paste this one line into your terminal to get started...

>You're walked through every step of the conversion.

**Windows**
```
powershell -c "irm https://github.com/adamsthws/firmware-liberator/releases/latest/download/liberate.ps1 | iex"
```

**macOS**
```sh
sh -c "$(curl -fsSL https://github.com/adamsthws/firmware-liberator/releases/latest/download/liberate.sh)"
```
**Linux**
```sh
sh -c "$(wget -qO- https://github.com/adamsthws/firmware-liberator/releases/latest/download/liberate.sh)"
```
> If that says `wget: not found`, use the macOS line instead.

<details>
<summary>Or with git, or a zip</summary>

```sh
git clone https://github.com/adamsthws/firmware-liberator.git
cd firmware-liberator
sh start.sh       # Linux / macOS
.\start.cmd       # Windows
```
-- or -- [download as a zip](https://github.com/adamsthws/firmware-liberator/archive/refs/heads/main.zip), and double-click `start.cmd`
</details>

<br>

## What you'll see

**Tasmota:**

![A successful conversion to Tasmota](docs/SuccessRun.svg)

**ESPHome:**

![A successful conversion to ESPHome](docs/SuccessRunESPHome.svg)

<br>

## If you have to stop

E.g. you close the window, lose your Wi-Fi, run out of time - it doesn't matter. Run the
same command again - the one line above, or `start.cmd` / `sh start.sh` in the folder.

> It remembers which plug it was working on and what it had done. Where a step
was interrupted, it checks the plug to work out where to resume from.

<br>

## What you end up with

**Official:**  
[ESPHome](https://github.com/esphome/esphome) - or - [Tasmota](https://github.com/arendst/tasmota) - or - [OpenBeken](https://github.com/openshwprojects/OpenBK7231T_App)  - (Your choice)
  
\- and -

- A full backup of the plug's Sonoff firmware to restore later.

- Once flashed, ESPHome, Tasmota and OpenBeken recieve thier updates as normal.

- Converted devices are identical to ones flashed over USB.
> The final automated checks verify this by hashing at the end of the conversion. (Every block and every byte is verified).

<br>

## How it went

See how it went for others: **[Conversion Reports](https://github.com/adamsthws/firmware-liberator/discussions/1)**
> Reading the thread is the quickest way to see how it went for others.

Please report back on how it went for you! 
> A clean conversion, a run that stopped partway - all of it is useful. 

<br>

## Documentation

- **[what-happens](docs/what-happens.md)** - how it works and what
  happens to the plug, step by step.
- **[technical-details](docs/technical-details.md)** - the firmware
  bundle, secrets handling, memory layout, technical notes.
- **[troubleshooting](docs/troubleshooting.md)** - problems met so far
  and their fixes.
- **[emulator](docs/emulator.md)** - the whole conversion run against
  an emulated device.
- **[unattended](docs/unattended.md)** - running a conversion with
  nobody at the keyboard - for testing.
- **[testing](docs/testing.md)** - unit tests, the emulator, fault injectio, and real plugs.
- **[updating-firmware](docs/updating-firmware.md)** - how the
  Tasmota and ESPHome the tool installs are updated.
- **[upstream-contributions](docs/upstream-contributions.md)** - bugs
  found and fixed while building this project.
- **[legal](docs/legal.md)** - how the project remains lawful, and what it asks of you.

<br>

## If something goes wrong

Start with **[troubleshooting](docs/troubleshooting.md)**
> It covers the problems that came up while the tool was being built.

<br>

## Roadmap

This project has big ambitions to support a broad range of devices and firmwares. The difficult groundwork has been laid down
to support devices built on both **Espressif** and **Beken** chips. Next is expanding the list of supported devices.

> Also being considered: support for **Supla** firmware, **Zamel** modules and **Tuya** devices.

<br>

## Thanks

Thanks too to everyone in the Tasmota threads for the
[UK](https://github.com/arendst/Tasmota/discussions/22554) and
[EU](https://github.com/arendst/Tasmota/discussions/21255) S60, whose
open-the-case method came first.

A big thank you to [HG0161](https://github.com/HG0161) and the
[SonoffS60OTA](https://github.com/HG0161/SonoffS60OTA) repository for working out the OTA update mechanism (Worked out with the Tasmota community's discussion of this device).

Thanks to [lcgamboa](https://github.com/lcgamboa) for the
[QEMU fork](https://github.com/lcgamboa/qemu) that emulates the ESP32, and to [openshwprojects](https://github.com/openshwprojects) for the
[Beken emulator](https://github.com/openshwprojects/BekenEmulator). Every conversion is tested against them before it reaches a real plug.

Thanks to [p1otr](https://github.com/p1otr) for the ESPHome option and its
S60 config.

<br>

## To Sonoff and eWeLink

Thankyou for making such excellent devices. Please keep this update method open. Letting owners run their own firmware is a win for you as much as for them.

**It sells plugs**
> Tasmota began life as *Sonoff-Tasmota*: a great many people first bought Sonoff because they could reflash it, and the home-automation community still recommends the brand for that reason. Every plug this tool supports is one more reason to choose Sonoff over an alternative.

**It costs you less**
> A converted plug never calls your servers. You keep the sale and drop the years of cloud traffic, accounts and app support that would have come with it. Sell more, support less.

**The owners support themselves**
> Someone who installs their own firmware takes on its upkeep. Tasmota, ESPHome and OpenBeken are maintained by their own communities.

<br>