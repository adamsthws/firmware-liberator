# What actually happens to your device

Written for someone who wants to understand what they are about to do, without
needing to know anything about embedded firmware. Nothing here is required
reading - the tool works without it - but the one irreversible step is easier
to consent to if you know why it is irreversible.

## The problem

A Sonoff S60 arrives running Sonoff's own firmware, which talks to Sonoff's
servers. Replacing it normally means opening the case and connecting a wire to
the chip inside. That is fiddly, voids everything, and on a mains device it is
genuinely dangerous if you get it wrong.

There is another way in. The device already knows how to replace its own
firmware - that is how Sonoff ships updates. It will do it when the owner of
the device asks. You are the owner.

## Getting in

The device already knows how to replace its own firmware, and it does it when the
account that owns the device tells it to. When Sonoff ships an update, their
servers send the device an *upgrade command*: a message that names where to
download the new firmware and the exact fingerprint - a SHA-256 hash - the
download must match. The device fetches that address, checks the hash, and
installs what it gets.

Two things about that command are the whole way in. It only has to come from the
account that owns the device - your eWeLink account - and the download address and
hash inside it are not tied to Sonoff. So the tool, signed in as you, sends the
device its own upgrade command: one that names *your computer* as the download
address, and the hash of the tool's firmware. The device fetches that firmware
straight from your computer and installs it, exactly as it would a genuine
update.

Nothing is faked or bypassed. The device does every check it normally does - it
confirms the command came from its owner, and it verifies the firmware it
downloaded against the hash in the command. It simply has no reason to care that
the address is your computer and the firmware is Tasmota rather than Sonoff's.

Because the device downloads straight from your computer, **no router rule is
involved at all.** The tool points the device at your computer and serves it the
firmware; it never touches, redirects or depends on Sonoff's own download
address. This holds whether or not Sonoff has an update waiting for the device -
the address and hash in the tool's command decide what the device installs, not
whatever Sonoff happens to be offering.

> **A note on history.** Earlier versions added one rule to your router
> whenever Sonoff was offering the device an update. It was never how the tool's
> firmware was delivered - that has always come straight from your computer - only a
> guard against a second, unlikely possibility: that the device might *also* fetch
> and install Sonoff's genuine update on its own, in the same short window, from
> a one-time signed address only the device can produce. That guard was dropped to
> keep the process simple, since the owner's command already decides what is
> installed. If a future device ever turns out to race its own update during the
> conversion, the guard can be put back: [router-rules.md](router-rules.md)
> has the rule, how to test it and where the old step lives - and how a
> simpler router rule can stop a stock device installing Sonoff updates at all.

## Why it takes several stages

You cannot replace everything at once, because the device is running from the
memory you want to change.

Think of the device's memory as a shelf with two large slots for firmware. The
device runs from one and can write to the other. So:

1. Sonoff's firmware is in the first slot. The tool puts a small installer of
   its own in the second, and restarts into it.
2. The installer puts a full Tasmota back into the first slot.
3. Tasmota then copies itself into the second slot and restarts, so the entire
   first half of memory is now free to be rebuilt.

Every stage runs from somewhere that is not about to be overwritten. That is
the whole trick, and it is why the process looks like more steps than it
should.

## The step that cannot be undone

At the very start of the device's memory is a small table, a few thousand bytes
long, listing where everything lives. The bootloader reads it every time the device
powers on. Without it, the device does not know where its firmware is and will
not start at all.

The Sonoff layout divides memory into two firmware slots and no file storage.
The official Tasmota layout divides it differently: a small recovery firmware,
one much larger area for Tasmota, and space for files. To get the official
layout, that table has to be replaced.

There is only one copy of it. No spare, no backup the bootloader can fall back
on. Replacing it means erasing it and writing the new one - and for the moment
in between, the device has no valid table. That moment is about a second, inside
a step that takes a minute, and if the power fails during it, the device is
finished as far as Wi-Fi is concerned.

Everything the tool does around that second is designed to make it as brief and
as certain as possible: the new firmware is already in place and verified before
the table is touched, the replacement table is checked byte for byte in memory
first, and the device refuses the whole operation unless it is running from the
far end of memory, well away from everything being changed.

## How your Wi-Fi carries across

The device keeps the Wi-Fi network it was set up with in the eWeLink app. The installer reads and joins the saved WiFi network automatically.

The small temporary firmware that runs after the rebuild saves your Wi-Fi again, where official Tasmota reads it, and Tasmota joins your network by itself.

If any of that does not work, the tool falls back to asking you to
join the device's own setup network.

Each time the device restarts into another firmware, that firmware has to join
your Wi-Fi on its own, and a weak signal makes that less certain. So the tool
reads the device's Wi-Fi signal as early in the run as the device can report it,
and again at later stages. If one reading is weak (below -75 dBm) it suggests
moving the device nearer your router first, and asks whether to carry on - once
a run. The run's report gives the lowest reading.

## The run, step by step

This is what `python3 -m tools.autoflash run` does, in order, for a device still
on Sonoff's firmware (for a device already converted, see
[Changing firmware later](#changing-firmware-later)). The first three steps are
the same on every device; then each chip family has its own:
[an Espressif device](#an-espressif-device-the-s60), or
[a Beken device](#a-beken-device-the-s61), whose run is shorter.

Each step also has a short ID, which the tool shows as it runs and keeps in
the run's record. Side by side, by the numbers below:

| | An Espressif device | A Beken device |
|---|---|---|
| 1. What has to be in place | `A0` | `A0` |
| 2. Find the device | `A1` | `A1` |
| 3. Which firmware | `A3` | `A3` |
| 4. | Download the firmware: `A5` | Ask eWeLink what it runs: `B1` |
| 5. | Ask the device what it runs: `A2` | Send the firmware, the point of no return: `B2` |
| 6. | The installer, the point of no return: `C2` | OpenBeken started: `K1`; on the way to ESPHome, the interim firmware: `J1` |
| 7. | Back up the Sonoff firmware: `C3` | Back up the device's own data: `B4` |
| 8. | A first Tasmota: `D1`, `D4` | ESPHome fetched: `J2` |
| 9. | The bootloader `D5`, the Sonoff identity `K1`, the new layout `E1`, `E2`, `E3`; for ESPHome, ESPHome written: `I1` | ESPHome started: `K2` |
| 10. | Official Tasmota: `F2`, `F4`, `G1`; for ESPHome, ESPHome started: `I2` | |
| At the end | Every byte checked: `H1` | |

An ID names a step within its family: the same ID in the other need not be
the same step (`K1`).

### The start, on every device

1. **List what has to be in place first** - the eWeLink app and account, the
   device added with LAN Control on - and wait for a key press.
2. **Find the device on your network.** The tool signs in to your eWeLink account
   (it asks for your email - or phone number, for an account made with one - and
   password here, once for the whole run) and
   looks on your network for the devices in that account it can convert, matched
   by their eWeLink id, so no other device can be mistaken for yours. If it
   finds one it carries on by itself; if it finds several it asks which. A device
   that answers here while eWeLink still shows it offline - one that has only
   just been switched on - gets up to a minute to reach eWeLink before the tool
   asks you anything. If yours does not answer, the tool says why: offline in
   eWeLink, LAN Control off, or on a different network.
3. **Ask which firmware you would like** - Tasmota or ESPHome for an S60,
   OpenBeken or ESPHome for an S61. Your choices depend on which device eWeLink
   says yours is (`--firmware` answers in advance).

### An Espressif device (the S60)

Steps 4-8 are the same whichever firmware you choose.

4. **Download the firmware it needs**, on the first run only, and check every
   file against a known fingerprint.
5. **Ask the device what firmware it has** and where it downloads updates from.
   The tool stops here, before anything is written, unless the device is a
   kind it converts - the Sonoff S60 or S61.
   It also reads the device's Wi-Fi signal, from what Sonoff's firmware
   announces on your network (encrypted with the device's own key, which your
   eWeLink account holds). If the signal is weak, now is the best time to fix
   it by moving the device, while nothing has been written.
6. **Replace the Sonoff firmware** with the tool's small installer. *This is the
   point of no return,* and the one place the tool asks you to confirm: after a
   short warning - which also says the Sonoff firmware is backed up first,
   or already was - you type PROCEED, which covers the whole conversion,
   including the backup in step 7 and the rewrite in step 9. Nothing is sent
   to the device before that. If your computer's firewall is on, the tool adds a
   temporary rule for the device first.
7. **Back up the Sonoff firmware.** The installer, which has joined your
   Wi-Fi by itself, hands over a copy of the device's whole memory - Sonoff
   firmware, settings and all - checked byte for byte against the device, and
   kept under the device's MAC address in `captures/devices/` (see
   [Your Sonoff backup](technical-details.md#your-sonoff-backup)). A device
   backed up before keeps that backup and is not read again; `--no-backup`
   skips it.
8. **Install a first Tasmota through the installer**, and give it your Wi-Fi
   details so it joins too. If the installer could not join, or Tasmota does
   not appear, the tool asks you to join the device's own network instead, names
   it, does the rest itself, and tells you when to switch back.
9. **Update the bootloader**, the small program that starts the device's
   firmware, to ESPHome's, which starts Tasmota, ESPHome and Sonoff's firmware
   alike and rolls back an update that fails to start. There is only one, so
   it is fetched and checked whole first, then written in under a second and
   read back; if it does not read back right, the old one is put back. A
   power cut in that second would leave a device that has to be opened to
   repair. Then **keep the device's Sonoff identity** - its eWeLink id and key, model
   and kind, a few hundred bytes of Sonoff's settings, never your Wi-Fi - in the
   last 4 KB of its memory, taken from its backup on your computer, or from the
   device's own settings if there is none. With it the device can go back to Sonoff's
   firmware later even if your computer no longer has its backup
   ([Changing firmware later](#changing-firmware-later)). Then **rebuild the device's
   memory layout** to match the official Tasmota one, with one change: the
   filesystem ends 4 KB early, and that last 4 KB is an area of its own, holding
   the identity. The
   tool counts down ten seconds first - Ctrl-C stops it. Before writing over
   anything, it copies the device's settings, and the note that says which half
   to start, to where the rebuild does not reach, and points the device at the
   copy: a power cut during the rebuild leaves the device starting the same
   Tasmota, settings and all. The tool waits for it to come back, puts its
   settings back where they were and tries again by itself - up to three times
   in all, for a power cut, a read-back that differed, or the device changing
   since its checks. Only the moments the layout table itself is written - a
   few milliseconds, twice each time - would leave a device that has to be
   opened.
10. **Install official Tasmota**, which joins your Wi-Fi by itself, apply the
   device's template and settings, and check the result. Only if it does not
   appear on your network are you asked to join its setup network. Then
   **calibrate its meter** as Sonoff's firmware did: by the factor the device's
   board calls for, read from three of its pins, and by its own factory
   calibration, from its Sonoff backup.

At the end the tool checks the finished device against the official Tasmota
release, tells you whether every byte matches, and removes the temporary firewall
rule - which needs administrator rights again, so it says why before the system
asks.

Then it asks what next: convert another device, or read one out. Either starts
on the next device, looking for it afresh, with your eWeLink sign-in kept so you
are not asked for it again; `q` ends. A readout asks the same.

For **ESPHome**, after the bootloader, steps 9 and 10 are instead:

9. **Keep the device's Sonoff identity** in the last 4 KB of its memory, as on the
   way to Tasmota. ESPHome keeps Sonoff's settings, identity and all, but loses
   them when you later move it to another firmware; this copy stays. Then
   **install ESPHome** into the half of the device's memory Tasmota is not
   running from - this project's ESPHome firmware for the S60, with your Wi-Fi
   written in, in your computer's memory only. Every block is read back and
   compared with that firmware, byte for byte. Only then does the device switch to ESPHome, by clearing the
   note that says which half to start, and restart. Until that moment it
   still starts Tasmota. The memory layout itself is not changed.
10. **Check ESPHome is running** and has joined your Wi-Fi. If it cannot, it
   puts up its own network, named like `esphome-abcdef`, where its page
   asks for your Wi-Fi. Then give it the device's factory calibration from its
   Sonoff backup, which it uses where the device's own memory has lost it (on
   a device converted through Tasmota first).

The tool then switches the relay off and on again through ESPHome to check it,
and gives you the device's ESPHome page.

### A Beken device (the S61)

Sonoff's Beken firmware does no LAN Control, so step 2 finds the device by its
eWeLink id in your account, not by its answer on your network, and everything up
to the new firmware goes through eWeLink
([the route](../tools/autoflash/routes/beken/)). Steps 4-7 are the same whichever
firmware you choose.

4. **Ask eWeLink what the device runs** - its model and firmware, which must be
   the kind's. For a device with no public dump, the official update file's
   partition table is checked too, and the run stops before anything is written
   unless it is the one the tool's firmware is built for.
5. **Send the new firmware through eWeLink**, wrapped as Sonoff's updates are,
   from your computer, after a temporary firewall rule if your firewall is on.
   *This is the point of no return,* and the one place the tool asks you to
   confirm: you type PROCEED, and nothing reaches the device before that. Sonoff's
   firmware stages the image in its download slot, and on the restart that follows
   the device's own bootloader installs it in place of Sonoff's firmware. A run to
   ESPHome sends OpenBeken first, as the interim firmware, with a marker by which
   it knows itself as that.
6. **Check OpenBeken has started** and joined your Wi-Fi by itself: on its first
   boot the tool's driver in it reads your Wi-Fi from Sonoff's own store, with the
   meter's factory calibration, and applies the device's wiring
   ([the driver](../tools/beken/openbeken/README.md)). The tool asks the driver
   whether the calibration is the one in Sonoff's store, which the report says.
7. **Back up the device's own data** through OpenBeken: its factory record, Sonoff's
   stores and the system store (`0x3e0000`-`0x400000`), kept under its MAC address
   in `captures/devices/`, as an Espressif device's backup is, with Sonoff's firmware
   beside it - made from the public dump of a stock S61, or the device's official
   file, as its own app was overwritten in step 5. The read is checked against the
   device's MAC in its factory record. A device backed up before keeps that backup;
   `--no-backup` skips it.

A run to OpenBeken ends there. For **ESPHome**:

8. **Have the interim firmware fetch ESPHome**, which it stages in the same
   download slot; just before the restart that installs it, it erases its own
   settings, so it leaves nothing of itself.
9. **Check ESPHome is running** and has joined your Wi-Fi, which it reads, with
   the calibration, from Sonoff's store on every boot.

## At the end

On an Espressif device converted to Tasmota, the tool reads back four parts of the
device's memory, calculates a fingerprint of each, and compares them against the
files they came from:

- the bootloader
- the memory layout table
- the recovery firmware
- Tasmota itself

If all four match, the device's Tasmota is byte-for-byte identical to a device
flashed over USB with the official release - not equivalent, identical - with
ESPHome's bootloader starting it. The one difference is in the layout table,
which has the area for the Sonoff identity after a filesystem 4 KB shorter.

From then on it is an ordinary Tasmota device: normal updates through its own
web page, normal integrations, and none of it touching Sonoff's servers.
Tasmota's updates, its `Reset` commands and its filesystem leave the identity
alone. Two things do not:

- **Tasmota's Partition Wizard** offers to grow the filesystem by those 4 KB
  ("Resize FS to max"). Doing it removes the identity's area from the table.
  The identity is still there at first, but the filesystem spreads its writes
  over all of its space, however empty it is: in a test, saving files of about
  80 KB in all wrote over it. Leave that button alone.
- **Writing Tasmota's factory image over the device**, with the web installer say,
  puts Tasmota's own table back. The identity is still there, and the tool still
  finds it, but the filesystem can now write over it in the same way.

Either way your backup on the computer, or your eWeLink account, can still take
the device back to Sonoff's firmware.


## Changing firmware later

A device this tool converted can move to either of the other two, or back to
Sonoff's firmware. The tool finds your device on the network by how it answers,
lists what has to be in place, then tells you what it runs and offers the other
two - steps 1 and 3 above, with no eWeLink in between. Every route has the same
checks as above.

Going back to Sonoff's firmware writes the device's own backup from your computer
when it is there. Without it, the tool puts the same together: Sonoff's official
firmware for your device, which it downloads from eWeLink as the device itself would
(or else from the public copy of that file the FlashDumps project keeps), Sonoff's
layout, and settings holding the device's identity and the Wi-Fi it is on now. The
identity is the one the device keeps; failing that, the tool rebuilds it from your
eWeLink account, which knows the device's id, key, model and kind, as long as the
device is still in it. The run says which it used. Sonoff's firmware is offered
either way. Once you choose it, before anything is written, the tool finds where
the identity comes from: on Tasmota it reads the one the device keeps, or else signs
in to your eWeLink account; with neither, it stops there. From ESPHome the kept
identity can be read only part way through, so the tool checks your account and,
if that has no record of the device, counts on the one the device keeps - every device
this tool converted keeps one. If it turns out there is none after all, or
Sonoff's firmware cannot be downloaded, the tool puts the device back on the
firmware it came from - ESPHome, or Tasmota - rather than leave it part way, and
says so at the end. Sonoff's firmware then
starts with its own settings: names, timers and energy history start afresh, and
the meter's factory calibration comes back only with the backup.

If your device runs Tasmota, the tool also offers to keep Tasmota (`--firmware tasmota`
chooses it in advance). That changes only the meter: the tool calibrates
it as step 10 does, if it is not calibrated already - on a device converted before
the tool did that, say. A meter calibrated by hand, against another meter, is
left as it is.

If your device runs an earlier release of this project's ESPHome, the tool offers
the release it installs instead (`--firmware esphome` chooses it in advance). It
sends it to ESPHome as an ordinary ESPHome update, as Home Assistant would send
one, with your Wi-Fi read from the device and written in. ESPHome writes it into
the half of its memory it is not running from and starts it, keeping its
settings. On an S60, a power cut before the new release has run for a minute
takes the device back to the release it had, so the tool waits that minute out.
The device reports its release and when it was built through its native API. A
device adopted into an ESPHome dashboard is not offered this, with an encryption
key or without one, because its build is not one this project published: the
firmware the tool installs would take away the dashboard's changes and any key,
so you update the device from the dashboard instead. To install this project's
firmware over it anyway, give `--replace-own-build`, which tells you what you lose
and asks you to confirm. It replaces only this project's ESPHome for that kind of
device, never another project's. From 2.3.0 the dashboard uses
this project's config at the release the device's firmware came from. The device gets
a fix when you choose a newer release, and not before
([how, and how to change a device adopted before](home-assistant-adoption.md)).

On an S61 the same goes for an earlier release of this project's OpenBeken
(`--firmware openbeken`): OpenBeken fetches the release the tool installs from
your computer, as it fetches any update, and restarts into it, keeping its
settings. The tool tells which release the device runs from its build, and that
it is this project's OpenBeken, on an S61, from its driver. Update it this way
rather than from the OpenBeken project: OpenBeken's own builds have neither the
driver nor the S61's memory layout.

If your device runs an official Tasmota older than the release this tool installs,
choosing Tasmota updates it to that release (`--firmware tasmota` chooses it in
advance). Tasmota restarts into its safeboot, which takes the new Tasmota from
your computer as the conversion's last steps give it, and the tool checks its
meter as above. Its settings and the device's template stay. The tool offers this
only for Tasmota's own release builds - a build of your own is left as it is. The
Firmware Upgrade on Tasmota's page does the same from Tasmota's site, and keeps
working.

Most routes pass through the same place the first conversion does: Sonoff's
memory layout, with a full Tasmota running from its second half. From there
the steps above finish the job for Tasmota or ESPHome, and going back to
Sonoff writes the device's own firmware and settings from its backup into the
first half, checks them, and switches to them.

- **From ESPHome**, the tool sends the startup firmware to ESPHome as an
  ordinary ESPHome update, with your Wi-Fi written in. This project's
  ESPHome gives its Wi-Fi name and password through its native API - encrypted,
  where the API has an encryption key, with that key. With an ESPHome that does
  not, the tool takes them from the device's Sonoff backup on your computer - the
  Wi-Fi the device was set up with - when that is shown to be the device's network
  still: the firmware bundle on your computer was set up for exactly those
  details, or ESPHome says it is on that network. Otherwise the tool asks you
  for them - or, with an ESPHome whose API is encrypted, for its key, and then
  ESPHome gives them. The startup firmware joins your Wi-Fi and saves it for
  Tasmota, and the tool sends it the full Tasmota.
- **From Tasmota**, the device is on Tasmota's own layout, where Tasmota sits
  across both halves of Sonoff's. A small ESPHome, the *mover*, fits in front
  of it: it is written there, the area Sonoff's layout keeps settings in is
  cleared, and Sonoff's layout table is written last - the one moment a power
  cut would leave a device needing to be opened. The device restarts into the
  mover, under whichever bootloader it has. To ESPHome, on a device that already
  has ESPHome's bootloader, the mover takes ESPHome itself as an update. On the
  way to any other firmware, or on a device with another bootloader, it takes the
  startup firmware as on the way from ESPHome, and the full Tasmota then
  installs ESPHome's bootloader, so that every device the tool converts ends with
  the same one.
- **A device whose layout puts its first app slot too near Tasmota** (the TH Elite
  and Dual R3) has no room for the mover there. Its mover goes where the S60's
  does, under a temporary layout table with the first app slot moved there. The
  second slot is in the same place in both, so the full Tasmota runs from that
  and writes the device's own layout table, last, as before.
- **A device left part way** - running the mover, the startup firmware or the
  full Tasmota on Sonoff's layout - is recognised when you run the tool again,
  and can go on to any of the three from where it is.

If your device's firmware has a password - a Tasmota web password, or an ESPHome
update password or web login, set in Home Assistant, say - the tool asks you for
it: a web password or login when it finds the device, ESPHome's update password
before the go-ahead to leave ESPHome. The question has a second choice, for once
you have removed the password on the device yourself: try again without one. Each
try is checked on the device, before anything is written. A password you type is
held in memory for the run only, and sent only to a device that has asked for one.

An ESPHome with an API encryption key (`key:` under `api: encryption:` in its
YAML config) encrypts its native API, and from ESPHome 2026.9 takes its
updates encrypted too - and only those, with `encryption:` under `ota:`. Given
the key, the tool talks to it encrypted: ESPHome gives its Wi-Fi details again,
and the tool sends its update encrypted, for which ESPHome asks no update
password. Where the tool needs the key, it asks you for it as it does a password:
before the go-ahead, for an ESPHome that takes only encrypted updates - or, for
one that takes them too, instead of its update password - and before it would
ask you for the Wi-Fi details, to read them off the device instead.

`AUTOFLASH_TASMOTA_PASSWORD`, `AUTOFLASH_ESPHOME_OTA_PASSWORD`,
`AUTOFLASH_ESPHOME_WEB_USER` with `AUTOFLASH_ESPHOME_WEB_PASSWORD`, and
`AUTOFLASH_ESPHOME_API_KEY`, in `.env` or the environment, give them in
advance, as do `--web-password`, `--esphome-ota-password`, `--esphome-web-user`
with `--esphome-web-password`, and `--esphome-api-key`.

On a device running Tasmota, the tool reads the Wi-Fi signal first, before anything
is changed. On ESPHome it also reads it before anything is written: from its
Wi-Fi signal sensor, which this project's ESPHome has, or else from
what ESPHome prints of its configuration when asked through its native API -
encrypted, with its key, where it has one. On an ESPHome that gives neither, the
tool reads it once the full Tasmota is running, as the startup firmware cannot
report it either.

## Safety, and why you can believe the checks

- **Nothing is trusted, everything is checked.** Every firmware file is
  checked against a known fingerprint before use, and the finished device is
  read back and compared against the official release.
- **The risky steps need you to say yes.** The conversion only starts after
  you type PROCEED - not a y/n - below a warning that spells out the risk.
- **The device checks too.** Every stage that writes refuses to run unless the
  device is in exactly the state it expects, and says so rather than guessing.
- **Nothing private is published.** Each run keeps its records in `captures/`,
  which git ignores.