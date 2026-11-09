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

The tool first asks eWeLink what the device runs and which firmware it would
be offered, and downloads that firmware as the device would. Nothing is sent
to the device for this. For a device the tool has not seen before, that is the
whole readout.

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

- add an email address or a Telegram username, so you can be told if support
  for the device is added,
- send it without either, or
- quit and keep it unsent.

Before it sends, your computer spends 10 to 30 seconds working out a small
puzzle. This lets the server turn away floods without the tool needing a
password.

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
device. Nothing about you is stored except the (optional) contact information you chose to give.

The server takes a limited number of readouts each day. If it has had enough
that day, your readout stays unsent on your computer; send it the next day.
If the project already has enough readouts of your device, the server thanks
you and does not store it.
