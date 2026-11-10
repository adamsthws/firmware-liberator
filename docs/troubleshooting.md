# When it goes wrong

Every problem listed here actually happened while the tool was being built. The
screen only shows a line per stage, so when one stops, the full story is in
the run's log, `captures/devices/<MAC>/runs/<date>/run.log` - the tool prints its path, and it is
safe to share: what identifies you, your network or your device is written as
`<email>`, `<wifi-name>`, `<device-id>` and so on. Running
again with `--verbose` shows the same detail as it happens. The run's own
record is in `autoflash-state.json` in the same directory, with the phase evidence
beside it.

First thing to try, always: start it again - `start.cmd` on Windows, `sh
start.sh` on macOS or Linux (or `python3 -m tools.autoflash run`; the
examples below use that form, and the start scripts take the same options).

It picks up where it stopped. Re-running is safe - every step checks the device
before writing anything.

---

## Your computer

**"the device did not fetch the ... instructions from this computer"**

The device could not reach your computer. The tool adds a firewall rule itself
when it can see a firewall (ufw or firewalld on Linux, Windows Defender
Firewall, the macOS firewall), so this usually means a firewall it cannot see -
security software, or rules managed some other way - or that the addition was
declined. The message shows the command for the firewall it found. Nothing
was written; run again once the device can get through.

On Windows the tool's rule names Python itself, so Windows Security does not
ask whether Python may use networks. If it was asked once before and "Cancel"
was chosen, that left a rule blocking Python - and Windows applies blocking
rules before allowing ones. The tool finds such rules, turns them off while it
runs, and turns them back on afterwards, all within the one permission
question. Running the terminal as administrator does not help and is not
needed: the tool asks for administrator rights itself, only for its firewall
rule.

What the tool cannot change, it says, before it starts and again if the device
cannot connect:

- **Other security software** (Norton, McAfee, Bitdefender and the like)
  often has a firewall of its own, which can block the device whatever Windows'
  firewall allows. Allow Python in there, or pause it for the conversion.
- **A computer managed by an organisation** can have its firewall set, by
  Group Policy, to ignore rules added on the computer itself. Ask whoever
  manages it, or use another computer. A work computer may not allow
  administrator rights at all, which the rule needs.
- **A network or software that inspects encrypted connections** - common at
  work or school - makes eWeLink's and GitHub's certificates untrusted. The
  tool says so; use another network, or pause that software.

A problem not listed here may be new. Please open an
[issue](https://github.com/adamsthws/firmware-liberator/issues/new?template=bug-report.yml)
for it, and the fix will help everyone after you: when a run stops, the tool
shows a link to one with its title filled in, and puts the run's report on
your clipboard to paste in. To ask a question, start a
[discussion](https://github.com/adamsthws/firmware-liberator/discussions) of
your own.

On Linux, blocked connections show in the kernel log:

```sh
journalctl -k | grep "SRC=192.168.99.50"
```

**The computer's address changed mid-run**

A cable unplugged, or a move from wired to Wi-Fi. The tool works out your
computer's address afresh each time and says when it has changed, so nothing
needs doing. If it chooses the wrong connection, name the address yourself:

```sh
python3 -m tools.autoflash run --listen-ip 192.168.99.20
```

Stay on one connection from the rebuild onwards, ideally wired.

**Joining the device's network: "waiting for this computer to join"**

The tool carries on as soon as the device answers at 192.168.4.1, so it waits
until the computer has actually joined. If the computer keeps dropping off the
device's network, close any setup page the system opened by itself: on Tasmota's
setup network every page view makes the device scan for networks, which briefly
takes its own network away.

If your home network itself uses 192.168.4.x addresses and the computer also
has a wired connection, the two clash; unplug the cable while joined to the
device's network.

**An earlier installer's password is not on the clipboard**

Only an installer from an earlier release has a password on its network. Copying
it needs `xclip`, `xsel` or `wl-copy` on Linux (`pbcopy` and `clip` are built
into macOS and Windows). Without one, type it: `s60-ota-bridge`.

## The router rule (no longer used)

The tool no longer touches your router. The device downloads the tool's firmware
straight from your computer, so there is nothing to redirect - whether or not Sonoff is
offering the device an update. If you are following an older guide that told you to
add a destination-NAT rule, you can skip that step entirely.
[router-rules.md](router-rules.md) keeps the old instructions, says why the rule
was dropped, and covers a simpler rule that stops a stock device installing Sonoff
updates.

## A device from another region

The UK S60TPG and the European S60TPF have both been converted (see
[What you need](../README.md#what-you-need)). A device from
another region is expected to work the same way, and the tool checks the device
at two points rather than assuming:

- **"... is a ..., which this tool does not convert"** - the device reports a
  different firmware family. The run stops here and nothing has been written;
  the device is still on its Sonoff firmware.
- **The read-only check before the rewrite (step E1) stops the run** - the
  device's memory layout is not the one the S60TPG has. By then the device runs
  Tasmota and keeps working; only the move to the official layout is refused.
  Leave it as it is and report it rather than forcing past the check.

Either way, please open an
[issue](https://github.com/adamsthws/firmware-liberator/issues/new?template=bug-report.yml)
for it, from the link the tool shows when it stops. A device from another region
that converts cleanly needs nothing more: its run's report already tells the
project ([what the tool sends](what-the-tool-sends.md)).

## The device can't be found

**"Your device did not answer on this network"**

The tool lists each device in your eWeLink account that it converts, with what
it found:

- **"eWeLink shows it offline"** - the device is switched off at the wall, or
  not on Wi-Fi. Switch it on and wait for the app to show it online.
- **"answers at ..., but eWeLink shows it offline"** (under *"Your device is
  on this network but cannot reach the internet"* when it is the only
  problem) - the device is on your network but cannot reach the internet,
  usually because the router keeps it LAN-only. The conversion is sent to
  the device through eWeLink, so allow it internet access, wait for the app to
  show it online, then choose *Look again*. You can block it again once it
  runs Tasmota.
- **"is at ... but does not answer on the network"** - the device is on your
  network but LAN Control is off. Turn it on in the device's settings in the
  eWeLink app, then choose *Look again*.
- **"is online in eWeLink but was not seen on this computer's network"** -
  your computer is on a different network from the device, such as the 5 GHz
  or guest Wi-Fi, a different router, or a VPN. The message names the network
  the device is on.

**"No supported device in the eWeLink account ..."**

The device is in a different eWeLink account. The message lists what the
account does have; choose *Use a different eWeLink account*.

**"Could not reach eWeLink"**

The tool could not sign in to see your account, so it lists every Sonoff device
that answers on the network instead. The next stage needs eWeLink too, and
checks there that the device you chose is one it converts, so check your
computer's internet connection first.

**"did not answer within 180s"**

- Check it is actually powered on. This has caught everyone at least once.
- Its address may have changed. The tool records the device's hardware address
  at the start and finds it again on a new address by itself; it only asks
  when the device cannot be found at all. Your router's device list shows it
  under a name starting `tasmota-`.
- Your Wi-Fi may have gone, a router restarting, say. Part way through, the
  device has no network of its own to fall back on: it keeps trying yours and
  rejoins within seconds of it coming back, and the tool waits for it.

**The device's own network**

Wherever the tool asks you to join the device's own network, it names it. Part
way through a conversion - on the installer, the mover, the startup firmware,
or Tasmota not yet set up - the device goes by `autoflash-recovery-` and the
last four characters of its hardware address, such as
`autoflash-recovery-CDEF`, in your router's list, and the installer's and the
mover's own networks are called that too - as is Tasmota's, when it was
installed through the installer's own network. Once converted, the device is named
for its firmware and the last six characters of its hardware address -
`tasmota-abcdef` or `esphome-abcdef` - and so is its own network. A Tasmota
that started from its own defaults names it after itself instead, such as
`tasmota-ABCDEF-1234`; sometimes it shows up as `ESP_` and six characters one
higher, such as `ESP_ABCDF0` - the same network, and the tool looks for both.
All of them are open, with no password, and the device's page on them is at
`http://192.168.4.1`. (A device an earlier release left part way, or converted,
may show the name it gave: `Sonoff-Setup-XXXX` for its installer, mover or
ESPHome, or `S60-OTA-Bridge-XXXX` for an older installer, with the password
`s60-ota-bridge`. The tool looks for those too, and gives the password when it
sees one.)

## The first install

**"Device is offline; refusing to send" straight after a complete download**

That means it worked. The installer firmware does not talk to eWeLink, so the
cloud reports the device offline, and the tool refuses to send the update twice.
The tool checks for the installer's `autoflash-recovery-XXXX` network and, when it
can see it, carries on by itself. If your computer's Wi-Fi is off it cannot
look; turn it on and run again.

**"Device model is ..., not one this tool converts"**

Another Sonoff on the same account answered. The tool converts only the S60
and S61, and stops before sending anything. Run again and choose your device from the list.

**"Empty reply from server" when you try `curl` yourself**

Tasmota refuses commands that arrive without a `Referer` header, as a security
measure, and refuses by closing the connection - which looks exactly like the
device being switched off. Add the header:

```sh
curl -H "Referer: http://192.168.99.50/" "http://192.168.99.50/cm?cmnd=Status%2011"
```

The tool already does this.

**Tasmota never appears after the installer gave it your Wi-Fi**

The installer saves the Wi-Fi details it joined with into Tasmota's settings, so
Tasmota starts with them and joins your network by itself. If the details
stop working (say the router restarted onto another channel and refuses the
device), Tasmota keeps retrying them and never opens its setup network.

Switch the device off and on 7 times in a row, leaving it on for only 2-3
seconds each time, then leave it on. Tasmota counts quick restarts like that,
clears its settings on the seventh, and opens its setup network as on a
first install. The tool, still waiting, carries on with the setup network.

## Uploading firmware

**The upload finishes suspiciously fast and nothing changes**

A firmware upload to this device takes tens of seconds. If it "succeeds" in under
ten, it did not write anything. Tasmota needs the file's size passed in the
request, and silently commits nothing without it. The tool sends it; if you are
uploading by hand, use the device's own web page rather than a bare `curl`.

**"the device refused the upload"**

The message includes what the device said. Usually it means the file is wrong -
check `sha256sum -c SHA256SUMS` passes in `artifacts/` and that you are uploading the file the
tool named, not the `.factory.bin`.

## When a migration stage stops

The stages that check, stage and rewrite the device's memory run on the device
itself, and report back to your computer. When one stops, the device says why,
and the tool shows it:

**"... did not pass: the device says: ..."**

The device's own reason. Two kinds:

- **One of its checks refused** - "NVS changed since preflight", "source table
  changed", and the like. Nothing was written. Something changed on the device
  between stages; run again, and the earlier checks are done again first. If
  the same refusal comes back, report it rather than forcing past it.
- **"... returned HTTP -1 after N attempts"** - the device could not reach your
  computer. Usually security software or a firewall that changed mid-run, the
  computer going to sleep, or a weak Wi-Fi signal (the tool warns when it is
  below -75 dBm). These stages are safe to repeat, so the tool tries them
  again by itself, up to three times, before stopping.

**"the device stopped the ... without reaching this computer"** or **"the device
has sent nothing for 2 minutes"**

The device lost its connection to your computer, so its reason could not arrive.
The tool then reads the device's own console and shows the last lines, marked
`The device's console:`, and gives the device's reason from there ("its console
says: ..."). Then as above.

**How long it waits**

A stage keeps the device busy for under a minute, and while it does the device
answers nothing else. Kept busy for much longer, a real device stops answering
until it is switched off and on (its energy meter's readings pile up), so the
device never spends more than 20 seconds in all trying a request again. If its
Wi-Fi drops - a router restarting, say - the stage stops, and the tool waits up
to 5 minutes for the device to answer again, then runs it again by itself (three
tries in all). The tool checks on the device whenever it goes quiet, so a device
that has stopped is noticed within about 20 seconds; no stage is waited on for
more than 5 minutes in all. Waiting for you - to join a network, say - has no
time limit.

**"The device is not answering at all"**

A device can be left answering nothing - not even its web page - though it
still answers ping: kept busy too long, or left waiting on your computer when
the tool was stopped mid-stage. Part way through, the tool restarts a device that
has stopped getting anywhere by itself, where the firmware on it has a way to;
Sonoff's own firmware has none
([Espressif research](espressif-research.md#no-restart-command)). Give it two
minutes; if its web page still does not answer, switch it off and on. Wherever this can happen, doing that is safe: staging
writes only the spare half of memory, the rewrite downloads what it needs
before it writes anything, and installing the official startup firmware
carries on where it stopped. Then run the tool again.

**"The device is on the network but not answering yet"**

A stage stopped part way (Ctrl-C, say) keeps the device busy until it gives up,
for up to a minute - and while busy, the device answers nothing else. Run again straight away and the tool waits for it, then
carries on.

## Slots and restarts

**"the device is still running from slot 0 after 2 uploads"**

Each upload writes the *other* half of memory and restarts into it, so the slot
alternates every time. The tool handles one swap-back automatically. If it
reports this, uploads are not being committed at all - see the "suspiciously
fast" entry above.

**A step says it was interrupted**

The tool marks a step before it starts writing, so an interrupted run is
visible rather than silent. It checks the device: where the device shows the step
finished, it moves on; otherwise it does the step again by itself, which
is safe, as every step checks the device before it writes anything and refuses
if it is not as expected.

## After the rebuild

**The device is broadcasting its own Wi-Fi network**

Normally Tasmota joins your Wi-Fi by itself after the rebuild: the temporary
startup firmware saves your details where it reads them. If it could not (it
saves nothing when its own settings write fails, and the device then behaves as
before), Tasmota starts up asking to be configured. The tool names its setup
network; join it (open, no password) and the tool sends your Wi-Fi details
itself. If your computer cannot see Wi-Fi networks, the tool cannot spot it
either: when it asks what to do about a device it cannot find, choose *It is
showing its own Wi-Fi network*.
If the device cannot connect with them it says so and leaves the device waiting -
nothing is saved - and you can set the Wi-Fi up at `http://192.168.4.1`.

**Safeboot is showing instead of Tasmota**

Safeboot is the small recovery firmware. Seeing it after the rebuild is normal;
seeing it after the whole run has finished is not. It means the full Tasmota
was never installed - re-run and let the tool redo that step.

It is also what a converted device shows after another firmware's whole-flash
image - an ESPHome `factory.bin`, say - was uploaded through Tasmota's own
*Firmware Upgrade* page. Tasmota switches to Safeboot for a file that big,
which writes it over Tasmota - whether it then says "Upload Failed" or
"Upload Successful" - so the device comes back on Safeboot every time, even
switched off and on; `SetOption78` makes no difference. Its settings are
still there. Run the tool on the computer that converted the device, or with
`--device-ip` and the device's address: it sees Tasmota's own Safeboot, puts
official Tasmota back with those settings, and goes on to the firmware you
choose - or stops there, if you choose Tasmota. By hand, upload the ordinary
`tasmota32c3.bin` (not a `factory.bin`) on Safeboot's *Firmware Upgrade*
page.

To move a converted device to ESPHome, run the tool and choose ESPHome rather
than uploading ESPHome yourself: Tasmota's layout has a single place for its
firmware, and the tool moves the device to one ESPHome can update itself in.

Note that Safeboot has no Wi-Fi setup of its own. It relies on settings already
saved on the device. That is why the conversion uses a temporary version with
Wi-Fi details built in, and swaps it for the official one at the very end.

## The worst case

**Power was lost while the memory layout was being rewritten**

If the device does not come back at all after that step, Wi-Fi recovery is not
possible. The device would need opening and a USB-serial adapter connected to the
chip inside. See
[reference/recovery-1.1.1.md](../archive/docs/reference/recovery-1.1.1.md).

This is the risk you accepted at the start, and it is the only failure in this
process that cannot be fixed by running the tool again.

## A converted device that has lost your Wi-Fi

After a new router or a changed Wi-Fi password, the device cannot join any more.

- **ESPHome** opens its own network by itself after a minute and a half
  without your Wi-Fi (its name, such as `esphome-abcdef`). Join it, and its page
  asks for your Wi-Fi. It keeps trying your Wi-Fi meanwhile, so after a router
  restart it simply rejoins. It opens that network only once each time it
  starts: if it has done so already - your router was off for more than a
  minute since, say - switch the device off and on, and it opens it again a
  minute and a half later. (A fault of ESPHome 2026.10, which builds the
  ESPHome the tool installs; ESPHome 2026.11 opens it every time.)
- **Tasmota** keeps trying your Wi-Fi and never opens a network by itself, so
  a router restart cannot leave it waiting on one. Press its button 6 times
  quickly: it opens its setup network for 3 minutes (its name, such as
  `tasmota-abcdef`, or `ESP_` and six characters). Join it, browse to `http://192.168.4.1`
  and choose your Wi-Fi.

## A converted device that switches nothing

Tasmota answers and stays on your Wi-Fi, but has no relay or power meter: in
Home Assistant's Tasmota integration it shows only a restart button and its
Wi-Fi. This happens when Tasmota restarts six times in a row, each within 10
seconds of starting: it takes that for a boot loop and goes over to its fallback
module, which knows nothing of your device. A device converted by 2.1.2 or later has
its own template as that fallback and goes on switching. For a device converted
earlier, the check below puts the template back and makes it the fallback.
Or, in the device's web page under **Tools → Console**:

```
Backlog Template {"NAME":"Sonoff S60TPG","GPIO":[1,1,1,1,224,544,1,3104,1,32,1,0,0,0,0,0,0,0,1,1,1,1],"FLAG":0,"BASE":1}; Module2 0; Module 0
```

(`S60TPF` in the name for a European device: the pins are the same.)

## An ESPHome device with no page and no relay

If ESPHome is stopped within a minute of starting ten times in a row - by
switching it off and on, say - it takes that for a boot loop and starts in safe
mode: on your Wi-Fi with only its update service, no page, no relay, nothing in
Home Assistant. After five minutes it restarts as itself, with nothing lost;
leave it switched on. If you run the tool meanwhile, it says so and waits for
the device.

## Checking a finished device at any time

```sh
python3 -m tools.autoflash step H1 --device-ip 192.168.99.50
```

Reads four areas of the device's memory - the bootloader, the memory map, Safeboot
and Tasmota - and compares them against the known files, and checks Tasmota
has the device's template, as its fallback module too. It only reads, with one
exception: a device without its template (which switches nothing) gets it back,
keeping its other settings, and a device without it as the fallback gets that. Safe
to run whenever you want reassurance.
