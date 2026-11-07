# The emulators: converting a virtual plug

Two emulators run the whole conversion against an emulated Sonoff plug, so changes
can be tried end to end without converting, or risking, a real one. Each boots a
real flash dump of a stock plug, so it runs Sonoff's own firmware, unmodified apart
from the changes its emulator makes. They are development tools: they say nothing
about hardware the dump did not come from.

| | Chips | How it works |
|---|---|---|
| [The Espressif emulator](#the-espressif-emulator) | the ESP32-C3 and the classic ESP32, under QEMU | [tools/espressif/emulator/](../tools/espressif/emulator/README.md) |
| [The Beken emulator](#the-beken-emulator) | the BK7238, under BekenEmulator | [tools/beken/emulator/](../tools/beken/emulator/README.md) |

This page is how to use them. How each works - what it changes in a dump, its
patches to the emulator it is built on, and how it is run and debugged - is in its
README, beside its code.

## The Espressif emulator

`tools/espressif/emulator/` runs the ESP32-C3 (the S60) and the classic ESP32 (the
POW Elite, POWCT, TH Elite, Dual R3, NSPanel and M5, among others): which of the two
is read from the plug's flash ([Classic ESP32 plugs](#classic-esp32-plugs)). Its
plug runs Sonoff's firmware with
[the changes the emulator makes](../tools/espressif/emulator/README.md#what-is-changed-and-why).
A full run has been completed this way from a UK S60TPG on stock 1.2.0, through every
stage, to the official Tasmota on the official layout; how far each classic ESP32
device gets is in [espressif-research.md](espressif-research.md#espressif-devices).
It says nothing about hardware the dump did not come from, such as another regional
version's power meter.

### What you need

- **Docker,** on Linux, macOS, or Windows through WSL 2. Everything that is
  built or run lives in containers; nothing is installed on the host.
- **A Sonoff backup of a stock plug.** Every conversion keeps one for each
  plug, in `captures/plugs/<MAC>/sonoff-backup/`;
  `tools/espressif/flash_readout.py` takes one without converting (over Wi-Fi; the plug is
  put back to stock afterwards). `emu.sh` lists them by MAC address - on the
  plug's label, and in your router and the eWeLink app - and asks which plug
  to run when there are several (`--plug <MAC>` chooses beforehand; with nobody
  at the keyboard the plug run last is used). It runs a copy: the backup is
  never written. A dump set up the older way, `flash.bin` and `efuse.bin` in
  `captures/emulation/`, is offered too. Each plug's emulated flash is kept
  in `captures/emulation/state/` between runs, so switching plugs and back
  carries on where each was; `--fresh` puts the chosen one back to its
  backup. With no backup in `captures/plugs/`, the maintainer's sealed backup
  is fetched from its private package and filed there first, and a test copy
  `--plug` asks for that is not there yet is made from it (`copy_plug.py`):
  given its key and a login that can read the package, a checkout with an empty
  `captures/` runs the emulator ([private-backup.md](private-backup.md)).
  `AUTOFLASH_NO_RESTORE=1` says not to. For a device you do not own,
  `public_dump.py` makes a test copy from a public dump or update file
  ([adding-a-device.md](adding-a-device.md)).
- **The NVS partition generator** (and `cryptography`, for the cloud stub)
  in the repository's `.venv`, as the workflows pin them:

  ```sh
  .venv/bin/python -m pip install --require-hashes --no-deps -r requirements/ci.txt
  ```

#### Hardware

| | The emulator alone | With Windows beside it ("Windows from Linux") |
|---|---|---|
| Processor | any 64-bit one Docker runs on | x86-64 with hardware virtualisation (AMD-V/SVM or Intel VT-x) switched on in the firmware, so Linux has `/dev/kvm` |
| Memory | 256 MB for an ESP32-C3, 1 GB for a classic ESP32: the emulator's cap | 3.3 GB at most: Windows 2 GB, QEMU and the disk tools 1 GB, an ESP32-C3's emulator 256 MB. An 8 GB laptop with a desktop open manages it |
| Processors | 2, the emulator's cap | 2 more for Windows (caps, shared with everything else, not set aside) |
| Disk | about 0.5 GB: the image, and the plug's state under `captures/emulation/state/` | about 30 GB free while Windows installs; then about 21 GB: the ISO 8.2 GB, the layers 10.8, 0.9 and 0.4 GB, the images 0.8 GB |
| Downloads | the QEMU fork's source, once | Microsoft's ISO (8.2 GB), Windows Terminal (12 MB) and virtio-win's guest tools (32 MB), once; Python (11 MB) and OpenVPN (6 MB) each time a layer that gets them is set up |
| Time | see "How long it takes", below | |

Measured on an AMD Ryzen 5 4500U (6 cores) with 8 GB: Windows peaked at
2.1 GB, an S60's emulator at 80 MB, and the layers took 7, 2 and 1 minutes to set
up. The caps can be changed (below), and a computer short of memory is warned
before Windows starts.

#### How long it takes

For a computer like the one these were measured on - an AMD Ryzen 5 4500U
laptop with 8 GB, from 2020 - or faster. A faster one is nearer the low end.

**Once, the first time:**

| Step | Takes |
|---|---|
| Downloading the emulator's image, or building it (QEMU from source) where it is not published | under a minute; 2-3 minutes to build |
| Building the Windows VM's image | about a minute |
| Downloading Microsoft's ISO, 8.2 GB | depends on the connection: about 20 minutes at 50 Mbit/s, 2-3 at 500 Mbit/s |
| Installing Windows (layer `installed`) | 5-10 minutes |
| Setting up the `rig` and `tool` layers | 2-4 minutes together |

So the emulator alone is ready in a few minutes, and Windows beside it in
about 10-20 minutes plus the download.

`windows-test.sh`, and `emu.sh` while you are in it, keep the computer from
going to sleep: an install or a conversion cut off half way has to start
again. They hold a lock (`systemd-inhibit`) for as long as they run, and
change no setting, so nothing is left to put back; the lock goes when they
exit, however they exit. A flat battery cannot be held off, so on a laptop
running on its battery `windows-test.sh` says to plug it in.

**Each run:**

| Step | Takes |
|---|---|
| The emulator starting, until the plug is on its Wi-Fi | under 10 seconds |
| Windows from Linux: starting, until the link says Connected | 30-60 seconds |
| A conversion | about 7 minutes on a real plug, through all 15 stages; about 5 for an S60 in the emulator (4m 35s for an unattended run from stock) |
| Windows shutting down at the end | about 10 seconds |

**After changing what Windows is set up with:** the layers whose scripts
changed are set up again before the run starts - about a minute for `tool`,
2-4 for `rig` (and `tool` above it), or the full install again for
`installed`, from the kept ISO, without the download.

**The dump is private.** It holds the home Wi-Fi password and the plug's
eWeLink keys in cleartext. Keep it, and everything the emulator writes, under
`captures/` (Git-ignored): the emulator puts all its state in
`captures/emulation/state/`, and reads the backups without changing them. A
conversion run in the emulator keeps a backup of the emulated plug too, in
the emulator's own copy of the repository, never beside your real ones.

**The emulated plug is a clone of the real one** - same eWeLink device id,
keys and MAC. If both are online, they fight over one device in the account,
and an upgrade sent by the conversion could reach the real plug. Unplug the
real plug before using `--cloud`.

### Using it

On Linux or macOS (on Windows, see "On Windows" below):

```sh
tools/espressif/emulator/emu.sh            # plug offline: nothing reaches the internet
tools/espressif/emulator/emu.sh --cloud    # plug and tool online - asks you to confirm the real plug is unplugged
tools/espressif/emulator/emu.sh --cloud --yes  # ... without asking: you have unplugged it (for unattended tests)
tools/espressif/emulator/emu.sh --stub-cloud  # a local eWeLink stub serves this instance alone (its own cloud; see docs/cloud-stub.md)
tools/espressif/emulator/emu.sh --plug 58:cf:79:01:02:03  # run that backed-up plug, without asking which
tools/espressif/emulator/emu.sh --fresh    # put the plug back to its Sonoff backup, and forget the previous run and the eWeLink account
tools/espressif/emulator/emu.sh --pcap     # also record the plug's Wi-Fi traffic
tools/espressif/emulator/emu.sh --rebuild  # rebuild the image first
tools/espressif/emulator/emu.sh --detach   # start in the background; join: docker exec -it -u ubuntu autoflash-emu tmux attach -t autoflash
tools/espressif/emulator/emu.sh --no-firewall  # without the "computer's" firewall, which is on by default
tools/espressif/emulator/emu.sh --windows  # Windows is the computer (see "On Windows"; started by emu.ps1 and windows-test.sh)
tools/espressif/emulator/emu.sh --instance 2  # a second emulator beside the first (see "Several at once")
```

A conversion needs the cloud: the upgrade to the bridge firmware is sent
through eWeLink. Use `--cloud` for the real one (the real plug must be
unplugged, since the emulated one is its clone), or `--stub-cloud` for a local
stand-in that serves this instance alone — then nothing reaches the real
eWeLink, and any number of instances convert at once. See
[cloud-stub.md](cloud-stub.md). The first start builds the image, which takes a
few minutes.

#### Several at once

`--instance N` (1-30, on `emu.sh` and `unattended-test.sh`) runs another
emulator beside the first: its own container (`autoflash-emu-N`), state
(`captures/emulation/state-N/`) and browser port (8080+N), so tests can run side
by side. Each is its own small network, so the identical plugs do not meet.
They do meet in eWeLink, though: every clone has the same device id, the cloud
keeps one connection per id, and the upgrade at C2 is sent through it - so it can
reach another instance's plug. Run the eWeLink stages, up to C2, one instance at
a time; after C2 nothing uses the cloud. A new instance has no plug yet, so name
it with `--plug MAC` when there is more than one backup. An S60's takes about
0.6 of a CPU and 115 MB; a classic ESP32's runs two cores and is allowed 1 GB.
Run no more at once than your computer has cores: more makes runs fail that
would otherwise pass.

Every instance, and the Windows container beside one, is on a Docker network of
the emulator's own, `autoflash-emu`, which `emu.sh` makes the first time on
any computer: a /24, on a subnet that none of the computer's routes or other
Docker networks use (198.18.60.0/24 where it is free), kept for the next run as
the image is. Docker's default bridge is a /16, and the conversion tool in the
container sweeps the network it is on at its real size, up to a /22: some 2 s
more a run. `AUTOFLASH_EMU_NETWORK` names another network to use instead.

The terminal becomes a tmux session. Window **conversion** is "your computer
on the home Wi-Fi": run the tool there exactly as on a real plug,

```sh
python -m tools.autoflash run    # or just: run  (options pass through: run --verbose)
```

and window **plug** follows the plug's serial output, with the dump's secrets
masked (Ctrl-b n / Ctrl-b p switch windows; Ctrl-b d leaves, keeping the
plug's flash). If you started it with `--detach`, join it with
`docker exec -it -u ubuntu autoflash-emu tmux attach -t autoflash`, and Ctrl-b d
then leaves it running - not `docker attach`, which shows the session but
takes no typing. When the tool asks you to do something to the plug or your
Wi-Fi:

| On a real plug | In the emulator |
|---|---|
| Switch it off and on | `plug restart` (or `plug off`, `plug on`) |
| Restart your router, so the plug's Wi-Fi goes | `plug router off [SECS]`, `plug router on` |
| Power-cycle it quickly, N times | `plug cycle N` |
| Your desktop's Wi-Fi menu | `wifi`, `wifi join NAME`, `wifi home` |
| Your home Wi-Fi's name and password | `PICSimLabWifi`, empty password |
| Your computer's password, when sudo asks | `autoflash` |
| The plug's web page | http://localhost:8080 |

The tool knows when it runs in the emulator (`AUTOFLASH_EMULATOR_URL`), and its
final link points at http://localhost:8080 instead of the plug's own address.
Where it warns that sudo may ask for a password, it gives the emulator's
(`AUTOFLASH_EMULATOR_SUDO_PASSWORD`); on a real computer it never knows one.

#### Unattended, for testing

One command starts the emulator, converts the plug with nobody at the
keyboard, prints the screen the run left, and stops the emulator again
([testing.md](testing.md) says how that is used to test every route, and
every fault in its way):

```sh
tools/espressif/emulator/unattended-test.sh --fresh    # from stock, as a new user would
tools/espressif/emulator/unattended-test.sh            # from where the last run left the plug
tools/espressif/emulator/unattended-test.sh --keep     # leave the emulator running afterwards
tools/espressif/emulator/unattended-test.sh -- --new   # anything after -- goes to the run itself
```

It needs a `.env` with the eWeLink account the dump's plug is in (see
[unattended.md](unattended.md)). It starts the emulator with `--cloud --yes`,
so **the real plug must already be unplugged**: it is not asked. `--pcap`,
`--rebuild` and `--no-firewall` are `emu.sh`'s.

Once the plug answers on the emulated Wi-Fi, it runs
`tools/repo/unattended_run.py` in the container's copy of the repository, as your
user. `.env` is handed to that run alone (`--env-file`, from the container's
`/tmp`, removed afterwards): the copy of the repository never holds it, so a
run you start yourself with `emu.sh` asks for the eWeLink account and
everything else, as a real one does. The same goes for Windows: its copy of
the checkout has no `.env`. The run's sudo
password is the emulator's, and everything else is answered as
[unattended.md](unattended.md) describes. What `.env` holds of your real
network and plugs - the Wi-Fi details, a plug's Tasmota or ESPHome password,
web login or API encryption key - is left out: the emulated plug's are its
own. `AUTOFLASH_TASMOTA_PASSWORD`, `AUTOFLASH_ESPHOME_OTA_PASSWORD`,
`AUTOFLASH_ESPHOME_WEB_USER`, `AUTOFLASH_ESPHOME_WEB_PASSWORD` and
`AUTOFLASH_ESPHOME_API_KEY` set where the script runs go to the run instead,
for an emulated plug given them to test with. On a terminal the run is shown live;
otherwise, as in a CI log, the final screen is printed at the end, colours
and all. The recording stays in
`captures/emulation/state/work/captures/unattended/<time>/`, beside the run's
own `run.log`. The exit code is the run's: 0 when the plug was converted.

#### Public plugs

Every plug above starts from your Sonoff backup, and a plug kept with `--save`
holds Sonoff's settings and a copy of the backup. A **public plug** holds
nothing of Sonoff's or of your plug's, so it can go where your backup must
not. Past D5 a plug runs none of Sonoff's code - the installer and the interim
Tasmota have taken its app slots, and D5 its bootloader - so from there on
nothing of Sonoff's is needed: that is what tests the firmware a conversion
ends on ([updating-firmware.md](updating-firmware.md)).

`tools/espressif/emulator/sanitise_plug.py` makes one from a plug kept past D5, on a test
copy's made-up MAC (`copy_plug.py`). It rebuilds the flash from blank and
copies in only what it shows to be public: the bootloader, partition table and
app images byte for byte the published files in `artifacts/`, the startup
firmware the published one with the emulator's open network written in, the
boot selection, and the interim Tasmota's settings alone, naming only that
network. Sonoff's settings - its eWeLink keys, the Wi-Fi it was set up with,
its calibration - the copy of them the repartition keeps, and any leftover are
not copied. Of the eFuses it keeps the MAC, and what every chip of the kind shares - its
revision and package, which the bootloader checks, and the flash's type - and
nothing measured on the one chip (its calibration and factory ID); of the computer's files the run's state and its steps' reports,
with the account and eWeLink device made placeholders, not the Sonoff backup,
the logs or the firmware bundle. Then it searches everything it wrote for any
256-byte block of your dump, every identifying value of Sonoff's settings in
it, the real MAC in any form, and the eWeLink account and devices on your
computer, and writes nothing if it finds one (`--explain` says which setting
a value came from, never the value).

`tools/espressif/emulator/public_plugs.py` makes the two the final-firmware test starts
from - after E3 on the way to Tasmota, after D5 on the way to ESPHome - from
the fault suite's plugs saved after D1, and runs that test:

```sh
tools/espressif/emulator/fault_suite.py --plug=12:34:56:ab:cd:ef --stub-cloud prep   # once, with your backup
python -I tools/espressif/emulator/public_plugs.py make --plug 12:34:56:ab:cd:ef \
    --stock captures/plugs/<your plug's MAC>/sonoff-backup
python -I tools/espressif/emulator/public_plugs.py test                              # no backup, no cloud
```

They are made again only when what runs before the final firmware changes: an
interim firmware, or what a run keeps of itself. Then `public_plugs.py publish
--stock <backup>` shows them public once more and uploads them, as one
tarball named by its hash, to the repository's
[`emulator-public-plugs`](https://github.com/adamsthws/firmware-liberator/releases/tag/emulator-public-plugs)
pre-release, and prints the pin for `PUBLISHED` in `public_plugs.py`;
`public_plugs.py fetch` puts the pinned ones in `captures/`, checked - which
is how a checkout with no backup, such as a GitHub Actions runner, tests from
them (the `emulator-check` workflow). `unattended-test.sh
--from=public-...` runs one offline, with no cloud, real or stub, and no
`.env`; a plug kept from that run (`--save`) is public too, and `emu.sh
--plug-dir=DIR` starts one with no backup at all. A public plug cannot go
back to Sonoff: it has nothing of Sonoff's to go back to.

#### Classic ESP32 plugs

A classic ESP32 plug runs as an S60 does, with the same commands. Nothing
says which chip to emulate: `emu.sh` reads it from the bootloader's header in
the plug's flash (at `0x0` on an ESP32-C3, `0x1000` on a classic ESP32), and
picks the QEMU for it:

| | ESP32-C3 | Classic ESP32 |
|---|---|---|
| QEMU | `qemu-system-riscv32`, machine `esp32c3-picsimlab` | `qemu-system-xtensa`, machine `esp32-picsimlab` |
| eFuse file | 1024 bytes (a shorter one is padded) | 124 bytes, used as it is |
| Container memory | 256 MB | 1 GB: under that, the kernel kills QEMU part way through a download, which looks like the plug restarting |

The flash is the dump as it is, 4 MB on every plug so far. Some stock
firmware stops at start-up without the external RAM (PSRAM) its plug has:
the kind's `psram_mb` in [kinds.py](../tools/autoflash/kinds.py) gives it,
as QEMU's `-m` (4 MB on the NSPanel, 2 on the MINIR4). The kind is found by
the plug's eWeLink product model: a test copy's own (`plug.json`), the one an
earlier run recorded, or `AUTOFLASH_STUB_PRODUCT_MODEL`.

For a device you do not own, make a test copy from a public dump, or from
eWeLink's update file on another plug's flash, and run it by the made-up MAC
`public_dump.py` prints:

```sh
.venv/bin/python tools/espressif/emulator/public_dump.py sonoff-pow-elite
tools/espressif/emulator/unattended-test.sh --stub-cloud --fresh --plug=12:34:56:e3:2d:01
```

[adding-a-device.md](adding-a-device.md) takes a new device through this, and
says what to do when one does not boot. What the emulator needed to run classic
ESP32 firmware at all is in
[its README](../tools/espressif/emulator/README.md#what-a-classic-esp32-needed).

A device whose status in [`devices/status.json`](../devices/status.json) is
`Not ready yet` is turned down, by the emulated run as by a real one, unless
`AUTOFLASH_NOT_READY_YET=1` is set. `unattended-test.sh` passes it on to its
run; in a run you start with `emu.sh`, set it in the conversion window
(`AUTOFLASH_NOT_READY_YET=1 run`). Where each of those devices stops is in
[devices/not-ready-yet/](../devices/not-ready-yet/README.md).

### On Windows

To test the tool's Windows code paths (`arp -a`, `netsh` firewall rules
behind the elevation prompt, the Windows firewall itself), Windows can be the
computer on the emulated home Wi-Fi instead of the container. A wire carries
Ethernet frames between the emulator and a TAP adapter on Windows, so Windows
sees the plug's MAC, its mDNS answers and its connections exactly as on a
real network. There are two ways to have it:

- **Windows from Linux** - nothing to install by hand, and a clean Windows
  every run: `tools/espressif/emulator/windows-test.sh`.
- **On a Windows computer**, the emulator in WSL beside it:
  `tools\espressif\emulator\emu.ps1`.

Both check what is needed first, list what is missing, and get all of it
after one question - and, where administrator rights are needed, one sudo or
User Account Control prompt. Joining the plug's own Wi-Fi from Windows is not
emulated either way.

#### Windows from Linux

```sh
tools/espressif/emulator/windows-test.sh --cloud
```

The emulated plug runs in Docker as usual, and beside it **Windows Server 2025,
Server Core** in a container of its own (`tools/espressif/emulator/windows/`): QEMU with
KVM, so one layer of virtualisation, nothing nested. It needs Linux with
`/dev/kvm` and Docker; on a Mac, use a Windows computer or VM instead (below).
If `/dev/kvm` is missing on a PC, its processor's virtualisation is usually
off in the firmware set-up: "SVM Mode" on AMD, "Intel Virtualization
Technology" (VT-x) on Intel.

Both containers are capped, so they cannot take over the computer. Windows
gets 2 GB and 2 CPUs (`AUTOFLASH_WINDOWS_RAM=3G`, `AUTOFLASH_WINDOWS_CPUS=4` to change), and
its container at most 1 GB more, for QEMU itself, without swap. The emulator
gets 256 MB for an ESP32-C3, 1 GB for a classic ESP32, and 2 CPUs
(`AUTOFLASH_EMU_MEMORY=512m`, `AUTOFLASH_EMU_CPUS`).

```sh
tools/espressif/emulator/windows-test.sh --cloud                    # a conversion, in OpenConsole, as on Windows 11
tools/espressif/emulator/windows-test.sh --cloud --classic-console  # ... in the classic console, as on Windows 10
tools/espressif/emulator/windows-test.sh --cloud --first-run        # ... on a Windows that has never seen the tool
tools/espressif/emulator/windows-test.sh --cloud --fresh            # ... with the plug back to stock and no earlier run
tools/espressif/emulator/windows-test.sh --rebuild-layer=rig        # set Windows' rig layer (and tool) up again first
tools/espressif/emulator/windows-test.sh --stub-cloud               # the local cloud stub instead of eWeLink: a plug a Linux run converted against it
```

`--fresh`, `--pcap`, `--no-firewall`, `--rebuild` and `--plug=<MAC>` are `emu.sh`'s;
which plug runs is asked before Windows starts, when there are several.

The first run fetches Microsoft's **Windows Server 2025 evaluation ISO** - 8.2
GB, straight from Microsoft's Evaluation Center link, no sign-up, checked
against its pinned SHA-256, and kept - and sets Windows up from it in three
**layers**, each a disk in `~/.cache/autoflash-emu/windows` holding only its changes
to the one below:

| Layer | What it adds | Set up by | Takes |
|---|---|---|---|
| `installed` | Windows Server Core, as Setup leaves it: the user `autoflash`, who logs on by itself, and a hook that runs `start.ps1` at logon | the ISO, `unattend.xml`, `firstlogon.ps1` | 5-10 min |
| `rig` | what only the tests need: Remote Desktop, SSH, the clipboard over VNC (virtio-win's guest tools, pinned), no Windows Update or SConfig, and for the link OpenVPN's TAP driver, the "Autoflash emulator" adapter and a Python of its own, from the same `windows-bootstrap.ps1` a Windows computer uses | `rig.ps1` | a few min |
| `tool` | what the tool's own first run leaves: its portable Python - set up with progress bars on, as in a person's window, and failed if any shows | `tool.ps1` | about a min |

Every run starts from a throwaway layer on `tool`, with your checkout in
`C:\Autoflash\repo`, in about a minute. `--first-run` starts it on `rig` instead:
Windows has never seen the tool, so `start.cmd` gets its Python itself, as on
a new computer. When a layer's scripts change, the next run sets it up again
first, and every layer above it; `--rebuild-layer=installed|rig|tool` does
that anyway, from that layer up. A layer is replaced only once its set-up has
finished, so one that fails leaves the layers as they were.

Windows logs on by itself, opens the link and a PowerShell window for the
conversion - in **OpenConsole**, Windows Terminal's own console, as on Windows
11, or with `--classic-console` in Windows' classic one, as on Windows 10.
Only your computer can reach it, in any of these ways:

- **In your browser**, at **http://localhost:6080** - nothing to install. It
  is there while Windows installs and its layers are set up too. Copy and
  paste through the clipboard box in noVNC's side panel: the browser keeps
  pages from reading the clipboard directly, and what you paste stays
  Windows' clipboard while the page is open.
- **Over Remote Desktop**, `localhost:3389`, user `autoflash`, password
  `Windows-emulator`, with its own clipboard (`xfreerdp /clipboard`).
- **With a VNC viewer** on `localhost:5900`, with copy and paste through
  SPICE's agent in Windows.
- **Over SSH** ([below](#ssh-to-drive-a-run-from-a-terminal)).

The converted plug's own page is at its own address,
**http://192.168.60.50**, as the conversion's last link gives it: for as long
as the run lasts, your computer has a route to the plug's network through the
emulator, which forwards only your computer's connections to the plug (and
still nothing to the internet without `--cloud`). The route is set and
removed without sudo, and left out if your computer has a network of its own
at 192.168.60.0/24 - http://localhost:8080 works either way.

The terminal you started from is the emulator's, for `plug restart` and the
serial log; leaving it shuts Windows down. The conversion's captures are kept
between runs (`~/.cache/autoflash-emu/windows/captures`), so an unfinished one
can be resumed.

##### SSH, to drive a run from a terminal

Windows runs OpenSSH's server (set up in the `rig` layer) with PowerShell as
its shell, on `127.0.0.1:2222` (`AUTOFLASH_WINDOWS_SSH_PORT` to change), logged on
to as `autoflash` with a key `windows-test.sh` makes once in
`~/.cache/autoflash-emu/windows/ssh`. `tools/espressif/emulator/windows-ssh.sh` wraps it:

```sh
tools/espressif/emulator/windows-ssh.sh --wait          # until Windows has logged on and lets the key in
tools/espressif/emulator/windows-ssh.sh                 # PowerShell, in C:\Users\autoflash
tools/espressif/emulator/windows-ssh.sh 'Get-ChildItem C:\Autoflash\repo\captures'  # any PowerShell command
tools/espressif/emulator/windows-ssh.sh log -f          # follow the conversion's latest run.log
tools/espressif/emulator/windows-ssh.sh get C:/Autoflash/repo/captures ./win-captures
tools/espressif/emulator/windows-ssh.sh put tools/autoflash/cli.py C:/Autoflash/repo/tools/
```

The key is let in at logon (`start.ps1`), so about a minute after a run
starts. SSH is a session of its own, beside the console the run opens, not
in it: what the conversion shows there is read through its `run.log`. A
conversion can also be run over SSH itself (`windows-ssh.sh`, then
`cd C:\Autoflash\repo; .\start.cmd`), in the console SSH gives it, which is neither
OpenConsole nor the classic one.

Server Core has no desktop, which is what makes it quick to install and run,
and leaves three things a person's Windows has untested in this set-up: the User
Account Control prompt (Server Core has neither the prompt nor the service
behind it, and an administrator's programs are always elevated there, so the
firewall rule is added without asking; tests/autoflash/test_windows_uac.py answers it
every way instead, and a Windows 11 computer shows the real one), Windows
Security's notifications, and
Windows Terminal's window itself. Terminal cannot start on Server Core, which
lacks the XAML it draws with; OpenConsole, from the same release (pinned, 12
MB, fetched once into `~/.cache/autoflash-emu/windows`), is the same code for what
the tool's output depends on - colours, the escape sequences that redraw a
line or take a block back off the screen, wrapping - but not Terminal's tabs,
or its clickable links. Everything the tool does on the network - finding the
plug, the firewall rule, serving the firmware - is the same as on any Windows.

```
 ~/.cache/autoflash-emu/windows/
   server2025-eval.iso ─ Setup ─▶ installed.qcow2      Windows as Setup leaves it
                                    └─ rig.qcow2        + what the tests need
                                        └─ tool.qcow2   + the tool's Python
                                            └─ (a run)  thrown away when Windows shuts down
   captures/      the Windows conversion's captures, kept between runs
   windows-terminal.zip, *.built-from   OpenConsole's release; what each layer was set up from
```

The evaluation lasts 180 days from when Windows was installed; after that,
`--rebuild-layer=installed` installs it again from the kept ISO.

##### Starting over

| To get | Do |
|---|---|
| A clean Windows | nothing: every run starts from a throwaway layer, so nothing a run does - a firewall rule, a setting, a file - reaches the next one |
| The plug back to stock, and no earlier conversion to resume | `--fresh` (the plug's flash, the emulator's run, and the Windows run's captures; the eWeLink email is not offered either) |
| A Windows that has never seen the tool | `--first-run` |
| The test rig, or the tool's Python, set up again | `--rebuild-layer=rig` or `--rebuild-layer=tool`; changing their scripts does it by itself |
| Windows installed again | `--rebuild-layer=installed`, from the kept ISO |
| None of it on the computer | `docker rmi autoflash-win autoflash-emu`, and delete `~/.cache/autoflash-emu` |

Everything under `~/.cache/autoflash-emu` belongs to you, not root, though the
containers write it.

#### On a Windows computer

From the repository in PowerShell:

```powershell
tools\espressif\emulator\emu.ps1 -Cloud     # opens the emulator and the link, each in a window
.\start.cmd                       # once the link says Connected - as a Windows user would
```

`emu.ps1` first runs `tools\espressif\emulator\windows-bootstrap.ps1 -LocalEmulator`,
which installs what is missing: WSL 2 (one restart - run it again after, and
it carries on without asking), Ubuntu in it with Docker and the NVS
generator, Python if there is no 3.10 or newer (python.org's, for your
account), OpenVPN's TAP-Windows6 driver (pinned installers, checked by
SHA-256), and the "Autoflash emulator" adapter - 192.168.60.10/24, no gateway, so
Windows' internet stays where it is, and the lowest interface metric, so the
tool's mDNS goes out on it. While the emulator is not running the adapter
shows as disconnected and does nothing. `windows-bootstrap.ps1 -ListPython`
shows every Python it finds, and which it would use.

The emulator window is the usual tmux session, for `plug restart` and the
serial log. To wire a Windows computer to an emulator running elsewhere,
`windows-bootstrap.ps1` without `-LocalEmulator`, then
`windows_link.py --host ADDRESS`.

From a Windows checkout the emulator keeps its state - the plug's flash, the
serial log, the in-container run's captures - in `~/.local/share/autoflash-emu/state`
inside WSL (`\\wsl$\<distribution>\home\<you>\.local\share\autoflash-emu\state` from
Windows), not in `captures/emulation/state/`. WSL reaches the Windows drive
through 9p, where one flash write can take 100 ms, and the bridge then trips its
interrupt watchdog while writing Tasmota. The Windows tool's own captures stay
in `captures/` as usual.

WSL stops its VM, and Docker with it, soon after its last window closes: with
`emu.sh --detach`, keep a WSL window open.

```
 Windows: autoflash/cli.py ── "Autoflash emulator" TAP 192.168.60.10
                                   │  windows_link.py ⇄ 127.0.0.1:7860 (length-prefixed frames)
 container autoflash-emu ──────────┼───────────────────────────────────────
   windows-link ⇄ tap1 ─┐          │
                        br0 192.168.60.1 (dnsmasq, ufw) ── tap0 ── QEMU (plug .50)
```

### Known gaps

- **Open networks only.** The fork's access points have no WPA2, so the home
  network has no password. Every setup network the conversion makes is open
  from the installer 4.4.3 on, so all can be joined (`wifi join NAME`, and
  `wifi home` to go back); an earlier release's installer, with WPA2, cannot.
- **No power meter, buttons or screen.** No plug's meter (the S60's CSE7766,
  say) is emulated, so readings are absent and the end-of-run check's log says
  the meter is unchanged; the checks still pass, as the relay switches. A
  conversion passing says the firmware installs and starts, not that its
  wiring is right.
- **One plug, one region.** The emulator runs whatever the dump came from. A
  dump of another model or region is needed to emulate it. A test copy made
  from an update file runs on another plug's flash, so it tests the firmware,
  not the device's own layout or factory data.
- **No chip revision.** QEMU has no revision register, so a classic ESP32 says
  revision 0.0, and the installer's pin levels are empty
  ([espressif-research.md](espressif-research.md#what-a-backup-logs)).

## The Beken emulator

`tools/beken/emulator/` runs the conversion against an emulated Sonoff Beken plug:
[BekenEmulator](https://github.com/openshwprojects/BekenEmulator) at the commit
`UPSTREAM` pins, with this project's patches, as a container image. It boots the
public S61s dump, or a whole flash you give it, and runs Sonoff's firmware as a
real plug would, onto its Wi-Fi and eWeLink.

### What you need

- **Docker.** Each command first pulls the emulator's image, or builds it where it
  is not published.
- **No firmware of your own.** None is in the image or this repository:
  `bk-emu.sh dump` fetches the public S61s dump from upstream's repository, which
  publishes it, into `captures/emulation/beken-firmwares/` (the tools do this
  before they run), and `bk-emu.sh test` fetches upstream's other devices' dumps
  there too (about 280 MB). That directory is mounted into the container at the
  emulator's `firmwares/`, and the checkout at `/work`, as your own user;
  `S61_DUMP` names a dump of your own, under `captures/`.

### Using it

The tools in `tools/beken` run in its container:

```sh
tools/beken/emulator/bk-emu.sh python tools/beken/emulator/seed_join.py
tools/beken/emulator/bk-emu.sh run captures/your-dump.bin --wifi-peer auto
tools/beken/emulator/bk-emu.sh test -k "Sonoff 4MB"    # upstream's self-tests, under the patches
tools/beken/emulator/bk-emu.sh plug                     # the S61 on a home Wi-Fi of its own
tools/beken/emulator/bk-emu.sh computer tools/autoflash/cli.py run   # the tool, on that Wi-Fi
tools/beken/emulator/bk-emu.sh power cycle              # the plug switched off and on
tools/beken/emulator/bk-emu.sh reset                    # the plug as new, on Sonoff's firmware again
tools/beken/emulator/bk-emu.sh ui                       # OpenBeken's page, at http://127.0.0.1:8088/
```

`bk-emu.sh` with no argument lists the rest.

`bk-emu.sh plug` runs the dump as a plug provisioned on a home Wi-Fi, on a Docker
network of its own (`autoflash-beken`, apart from the Espressif emulator and
everything else), where eWeLink is a local stub and nothing reaches the internet
([how](../tools/beken/emulator/README.md#how-it-fits-together)). The plug boots
from its flash, kept in `captures/emulation/s61/autoflash-s61/` with its logs and
the stub's CA, so it carries on across firmware: each restart the firmware asks
for a boot of its own, from the bootloader, as on a real plug.

`bk-emu.sh computer` is a computer on that Wi-Fi: the tool there finds the
plug as on a home network, and its eWeLink is the stub. It works in its own
copy of this checkout (the plug's `work/`), so its runs' captures are its own;
an unattended run takes its eWeLink account from `work/captures/` (a made-up
one: the stub takes any). Sonoff's firmware, having fetched and staged a new
image whole and checked its CRC, restarts a second later, and the bootloader
installs it, as a real plug's does (the firmware's own code says so,
[beken-research.md](beken-research.md)). A plug
that does not come back is switched off and on - `bk-emu.sh power cycle`, as
the tool then asks; an attended run there offers to do it itself: the plug's
power switch is passed to the computer (`AUTOFLASH_EMULATOR_POWER`).

**The plug's web page goes through a proxy** (`bk-emu.sh ui`, `ui_proxy.py`),
OpenBeken's or ESPHome's - `ui` tells which by the name the plug gave its
router - never opened straight. The emulated plug runs the real OpenBeken, but far
slower than a chip: each request leaves a connection in TIME_WAIT on the plug,
lwIP has room for only a few, and on the emulator's clock they clear slowly. A
browser's parallel requests and the page's once-a-second polling fill them in
seconds, and the plug stops answering though its firmware still runs. The proxy
lets one request at a time reach the plug, at most one every 0.8 s, answers the
page's polling from a 3-second cache, and passes every action straight on;
[`ui_proxy.py`](../tools/beken/emulator/ui_proxy.py) says more. ESPHome's page keeps its live values
coming on one open connection (`/events`), which the proxy passes straight
through, one browser tab at a time.

`AUTOFLASH_BEKEN_DUMP` names another whole flash under this checkout for a new
plug to start from, one `rebuild_dump.py` made for another model, say
([Adding a 4 MB BK7238 device](../tools/beken/README.md#adding-a-4-mb-bk7238-device-with-no-whole-dump)).

### Known gaps

- **No Bluetooth.** The BLE controller's registers are modelled, but there is no
  radio: nothing is sent, and no phone can pair with the plug over BLE.
- **Slow.** It runs the real firmware far slower than a chip, which is why its web
  page goes through the proxy.
- **Nothing around the chip.** No relay, meter or button is wired to its pins, so a
  conversion passing says the firmware installs and starts, not that a device's
  wiring is right.
- **A Linux computer only.** `bk-emu.sh computer` is a Linux container; the
  Espressif emulator's [Windows](#on-windows) set-up has no Beken counterpart yet.
