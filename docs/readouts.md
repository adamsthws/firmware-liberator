# Readouts

A readout is an anonymised copy of your device's whole memory, with the
firmware eWeLink offers it, that you send to help add support for the device.
It gives the emulator the device as it really is (its partition table,
factory data, calibration and settings), which a public firmware file cannot.
A device the tool already converts helps too, if it runs another firmware
version.

## Before you start

Only go ahead with a device you can afford to lose.

A device read out in full has to be on your computer's network, as for a
conversion; one the tool has not seen before is asked about through eWeLink
alone.

## Making a readout

When a run finds no supported device in your eWeLink account, it offers to
read one out. To read out any device in your account, run this in the tool's
folder:

```sh
sh start.sh readout
```

On Windows, run `start.cmd readout`. If your account holds more than one
device, the tool asks which; `--device-ip <address>` picks one beforehand.

As a conversion does, a readout shows each of its stages on a line of its
own, the line under way turning until it is done
([`STAGES`](../tools/autoflash/readout.py) lists them). One that comes from a
conversion run carries on from that run's stage 2.

It ends as a conversion does, with a report of how it went - each device by
its model and firmware version, under whether the website accepted its readout
(its reference is in the run's log) - and anything left for you to do, such as
switching the device off and on once more. Like a conversion, each device's readout also sends the
project a short, anonymous report of how it went, without the readout itself:
[what the tool sends](what-the-tool-sends.md).

The tool first asks eWeLink what the device runs and which firmware it would
be offered, then asks the website whether a readout would add anything. The
website keeps three readouts of each model and firmware version, each from a
different person - three more each time eWeLink offers it a new release - and
now and then one more in full to check those were right. So a device like three already sent in is mostly not needed, nor a
second one from you, however many of that device you have. The tool stops
there.

Your eWeLink account still lists a device after it is converted, by this
tool or by hand. If you choose one that now runs Tasmota, ESPHome or
OpenBeken, the tool says so and reads none of its memory, as none of Sonoff's
firmware is left on it: its readout is what eWeLink says of it and the
firmware eWeLink offers it, which is still worth having.

Otherwise the tool downloads the offered firmware as the device would, except
any file the website already has, sending nothing to the device. For a
device the tool has not seen before, that is the whole readout. When the
website asks for links instead, such a readout downloads nothing: it sends
each offered file's link and SHA-256 alone, and the website fetches the
files from eWeLink itself once enough readouts agree on them. If any file's
link is not of the shape the tool knows, the readout downloads and sends the
files instead, and names that link beside its file for the project to check
([a link of another shape](#a-link-of-another-shape)).

A device listed as **Supported**, **Testers wanted** or **Readout wanted**
([devices](../devices/README.md)) is then read out in full. A variant of a
listed device that runs other firmware, such as its Matter version, counts as
one the tool has not seen. The tool says what it will do, and sends nothing
to the device until you type PROCEED. When the website asks for links, a
readout in full also sends each offered file's link in place of the file,
except a file whose link is not of the shape the tool knows, which it
downloads and sends, its link named beside it. It downloads no other, except
for a Beken device, whose Sonoff firmware the tool puts back from the offered
file.

### A link of another shape

A file eWeLink offers by a link of a shape the tool has not seen before is
still sent whole with a readout. Its link is sent beside it, for the project
to check before the website ever fetches from it, and only if it is plain:
an eWeLink file server or a public address, one of the ports eWeLink's links
use, and a path of letters, digits and `. _ - /` alone. The part after `?` or
`#` is left out. A link that holds anything the tool knows of your device or
your account, in any form - the device's eWeLink ID, its keys, its MAC
address or the next few either side of it, or your account's key or sign-in -
is never sent; nor is one that is not plain. The rules are `own_marks` and
`review_url` in
[`tools/ewelink/offered_firmware.py`](../tools/ewelink/offered_firmware.py).

### Every device at once

With two or more devices in your account, the first choice (`--all`
beforehand) is to read out every one, including those the tool converts: one
may run a firmware version the website has no readout of, and new firmware is
where a conversion most likely meets something it is not ready for.

Each device is read out in turn, the quick readouts first, each sent as soon
as it is ready. At a device's
PROCEED, type s to skip just that device, or q to quit, as at any question.

At the end, one report covers every device, listing each model and firmware
version once under what became of it - accepted, not needed (one like it is in
already), or not read out because the website has taken all it can for today,
takes 20 readouts a day from one place, or can take no more for a few weeks. Each readout gets its own puzzle ([Sending it](#sending-it)).

## What happens to your device

For a listed device, what the tool does depends on its chip:

- **ESP32-C3 or classic ESP32** (the S60, POW Elite, TH Elite and others):
  the tool installs a small reading program in the second firmware slot,
  losing only what that held (often the Sonoff version from before the last
  update). It reads the whole memory, which the tool keeps on your computer
  as the device's Sonoff backup, then asks you to switch the device off and
  on, and waits for its Sonoff firmware to answer.
- **Beken BK7238** (the S61): with only one firmware slot, a small program
  replaces the Sonoff firmware for a few minutes, switching nothing on the
  device. It reads the whole memory, which the tool keeps on your computer,
  then puts the device's own Sonoff firmware back, and the tool waits until
  the restarted device shows online in eWeLink. This is shown in the
  emulator.
- **Any other chip**: nothing is sent to the device. The readout is the
  firmware eWeLink offers it, and what the device and eWeLink say it is.

Whichever it is, the device keeps its Sonoff firmware, its settings and its
eWeLink pairing.

If a readout stops part way - Ctrl-C, say - the tool says how to get Sonoff's
firmware back. On an ESP32 device, switch it off and on: the reading program
sits beside Sonoff's firmware, which starts again. On a Beken device the
program stands in its place, so switching it off and on starts the program
again: run the readout again instead, and it finishes and puts Sonoff's
firmware back.

## What is taken out

The tool sends an anonymised copy, and keeps the original only on your
computer:

- **Your Wi-Fi network's name and password** are taken out wherever the
  settings name them, along with every other copy of them in the memory.
- **The device's eWeLink identity** (its device id and keys) is replaced
  everywhere by made-up values of the same shape.

Sonoff's firmware itself, code Sonoff publishes, is left as it is, and so is a
password a firmware keeps where the tool does not know to look; the readout is
kept private either way. A Beken device's factory record is left alone too: it holds
the device's own encrypted factory credentials, not yours, and the Sonoff
firmware does not start if the record is changed at all.

The emulator needs only how the device's memory is laid out, never which
device it is, and answers for eWeLink itself, so the copy never reaches
eWeLink.

## What is in one

A readout is one file, `readout.tar`, holding:

| File | What | ESP32 | Beken |
|---|---|---|---|
| `manifest.json` | what eWeLink and the device said they are (model, firmware version, chip), the tool's version, how many things were taken out, whether the device's identity could be rebuilt from your account as yes-or-no outcomes alone ([the identity check](what-the-tool-sends.md#the-identity-check)), and your contact if you gave one | yes | yes |
| `flash.bin.xz` | the whole memory, anonymised, compressed | yes | yes |
| `efuse.bin.xz` | the chip's fixed settings (its eFuses), as the reading program saw them, compressed | yes | |
| `info.txt` | what the reading program said about the device | yes | yes |
| `report.json` | the check of the memory read: its partitions and hashes | yes | |
| `offered/<name>` | each file of the firmware eWeLink offers the device, as eWeLink serves it; when the website asks for links, only those whose link is of a shape the tool does not know, and `manifest.json` lists the rest by link. `manifest.json` names a file's link of another shape beside it, as [above](#a-link-of-another-shape) | yes | yes |

A device whose memory was not read has only the manifest and the offered
firmware. Sent as links, it is not a file at all: the device's model, the
name and version of its Sonoff firmware, the device as a
[report](what-the-tool-sends.md#how-your-device-is-recognised) names it, and
each offered file's name, version, SHA-256 and path on eWeLink's server,
which is the same for every device on that release.

## Sending it

The tool sends the readout as soon as it is made, without asking: this page
says what it holds. Before it sends, your computer works out a small puzzle, which lets the
server turn away floods without a password. It usually takes 10 to 30
seconds, sometimes a minute or two, by luck and your computer's speed, while
the tool shows it is still working. If the connection drops on the way, the
tool sends it again a few seconds later; if the website is busy with another
readout from you, it tries again every 15 seconds, for up to a minute and a
half.

Once it is sent, the tool gives you the readout's reference to quote if you
post about the device.

A readout that could not be sent stays in the run's
folder as `readout.tar` (`readout-<device id>.tar` when every device was read
out at once), and the tool sends it the next time you run it, beside
whatever you do then: there is nothing for you to do. Several go one at a
time, half a minute apart and never while a readout of yours is being sent,
as the website takes two a minute; past its daily limit, the rest go another
day. The run's log says when each went. To send it yourself instead, run:

```sh
sh start.sh readout --send <path to readout.tar>
```

To keep it from being sent, delete its file.

A readout sent as links needs only a smaller puzzle, and one that cannot be
sent is not kept: read the device out again.

## Where it goes

The readout goes to the project's server at `api.firmwareliberator.com`,
which stores it privately, never publishes it, and uses it only to add
support for the device. It is kept for good, since a readout of one firmware
version can help with the device's later versions too. Nothing about you is
stored with it. Cloudflare,
which runs the server, logs each request with your internet address for
seven days, so that the maintainer can look into a failure.

Past the server's daily limit, yours stays unsent on your computer, to send
the next day. One of a device the project has enough readouts of already is
thanked for and not stored.

