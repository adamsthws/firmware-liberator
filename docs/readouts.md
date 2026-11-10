# Readouts

A readout is an anonymised copy of your device's whole memory, with the
firmware eWeLink offers it, that you send to help add support for it. It gives
the emulator the device as it really is: its partition table, factory data,
calibration and settings, which a public firmware file lacks. A readout of a
device the tool already converts helps too, if it runs another version of its
firmware.

## Before you start

Only go ahead with a device you can afford to lose.

Your computer needs to be on the same network as the device, as for a
conversion.

## Making a readout

Run the tool as usual. When it finds no supported device in your eWeLink
account, it offers to read one out. To read out any device in your account,
run this in the tool's folder:

```sh
sh start.sh readout
```

On Windows, run `start.cmd readout`. If your account holds more than one
device, the tool asks which; `--plug-ip <address>` picks it beforehand.

### Every device at once

If your account holds two or more devices, the first choice is to read out
every one of them, those the tool converts too: one may run a firmware version
the website has no readout of, and new firmware is what a conversion can meet
unready. You're asked once whether to
add a way to reach you, and then each device is read out in turn and sent on
its own as soon as it is ready. Devices that only get the quick readout below
go first. A device that can be read out in full still asks you to type PROCEED:
type s there to skip just that device, or q to quit, as at any question. At the
end the tool says what became of each one. It may say a readout wasn't needed,
because one like it is in already, or that the website has taken all it can
for today, or that it takes 20 readouts a day from one place. Each readout
gets its own puzzle before it is sent (see
[Sending it](#sending-it)). If you added your email address, you'll be told
when any of these devices' support changes, whether or not its readout was
needed.

The tool first asks eWeLink what the device runs and which firmware it would
be offered. It then asks the website whether a readout of it would add
anything: the website keeps three readouts of each model and firmware
version, each from a different person, so a device like three already sent
in isn't needed, nor a second one of a model you sent in already - one
readout each is enough, however many of a device you have. Then nothing more is done,
but you can still leave your email address, to be told when the device is
supported. Otherwise the tool downloads the offered firmware as the device
would, leaving out any file the website has already. Nothing is sent to the
device for this. For a device the tool has not seen before, that is the whole
readout.

A device listed as **Supported**, **Testers wanted** or **Readout wanted**
([devices](../devices/README.md)) is then read out in full. The tool says what
it will do, and nothing is sent to the device until you type PROCEED.

## What happens to your device

A device the tool has not seen before is left alone: nothing is sent to it.
For a listed one, what the tool does depends on the device's chip:

- **ESP32-C3 or classic ESP32** (the S60, POW Elite, TH Elite and others):
  these keep a second firmware slot, so the tool installs a small program
  there that only reads, leaving the Sonoff firmware where it is. It reads the
  whole memory and keeps it on your computer as the device's Sonoff backup.
  The tool then asks you to switch the device off and on, and waits for its
  Sonoff firmware to answer.
- **Beken BK7238** (the S61): there is only one firmware slot, so a small
  program replaces the Sonoff firmware for a few minutes. It switches nothing
  on the device. It reads the whole memory, which the tool keeps on your
  computer, then puts the device's own Sonoff firmware back. The device
  restarts, and the tool waits until it shows online in eWeLink again. This
  has been proven in the emulator, but not yet on a real Beken device.
- **Any other chip**: nothing is sent to the device. The readout is the
  firmware eWeLink offers it, and what the device and eWeLink say it is.

Either way, the device keeps its Sonoff firmware, its settings and its
eWeLink pairing.

## What is taken out

The tool sends an anonymised copy, and keeps the original only on your
computer:

- **Your Wi-Fi network's name and password**, wherever the settings name them,
  and every other copy of them in the memory.
- **The device's eWeLink identity** (its device id and keys) is replaced
  everywhere by made-up values of the same shape.

Sonoff's firmware itself is left as it is: it is code Sonoff publishes. If a
firmware keeps a password somewhere the tool does not know to look, that copy
goes as it is; the readout is kept private either way. On a Beken device, the
device's factory record is kept as it is: it holds the device's own encrypted
factory credentials, not yours, and the Sonoff firmware does not start if it
is changed at all.

The emulator never needs who the device is, only how its memory is laid out:
the copy never reaches eWeLink, as the emulator answers for eWeLink itself.

## What is in one

A readout is one file, `readout.tar`, holding:

| File | What | ESP32 | Beken |
|---|---|---|---|
| `manifest.json` | what eWeLink and the device said they are (model, firmware version, chip), the tool's version, how many things were taken out, and your contact if you gave one | yes | yes |
| `flash.bin.xz` | the whole memory, anonymised, compressed | yes | yes |
| `efuse.bin` | the chip's fixed settings (its eFuses), as the reading program saw them | yes | |
| `info.txt` | what the reading program said about the device | yes | yes |
| `report.json` | the check of the memory read: its partitions and hashes | yes | |
| `offered/<name>.xz` | each file of the firmware eWeLink offers the device | yes | yes |

A device whose memory was not read has only the manifest and the offered
firmware.

## Sending it

The tool shows you what goes - "the device's firmware readout, anonymised" -
and asks you to choose one of:

- add an email address, so you can be told when support for the device
  changes,
- send it without one, or
- quit and keep it unsent.

Before it sends, your computer works out a small puzzle. This lets the server
turn away floods without the tool needing a password. How long it takes is
luck and your computer's speed: usually 10 to 30 seconds, sometimes a minute
or two. The tool shows it is still working. If the website is busy with
another readout from you, the tool tries again every 15 seconds.

Once it is sent, the tool gives you its reference: quote it if you post
about the device.

A readout you did not send, or that could not be sent, stays in the run's
folder as `readout.tar`. To send it later, run:

```sh
sh start.sh readout --send <path to readout.tar>
```

## Where it goes

The readout goes to the project's server at `api.firmwareliberator.com`. It
is stored privately, never published, and used only to add support for the
device. It is kept, not deleted after a while: a readout of one firmware
version can help with that device's later versions too. Nothing about you is
stored except the (optional) contact information you chose to give.

### Your email address

If you give your email address, you're first sent an email with a link to
confirm it, and once more the next day if you haven't yet. Nothing is kept
unless you follow that link: an address never confirmed is deleted after 7
days. Once confirmed, it is stored privately with
your device's model, apart from the readout, so you can be emailed when that
model's support changes: when it becomes supported, say, or a test version is
ready to try. It is used for nothing else. Every such email has a link to
remove your address, which asks you to confirm first. You can give it even
when no readout of your device is needed.

The server takes a limited number of readouts each day. If it has had enough
that day, your readout stays unsent on your computer; send it the next day.
If the project already has enough readouts of your device, the server thanks
you and does not store it.
