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

The tool first asks eWeLink what the device runs and which firmware it would
be offered, then asks the website whether a readout would add anything. The
website keeps three readouts of each model and firmware version, each from a
different person, so a device like three already sent in is not needed, nor a
second one from you, however many of that device you have. The tool stops
there, though you can still leave your email address to hear when the device
is supported.

A readout is of Sonoff's firmware. Your eWeLink account still lists a device
after you convert it, so if you choose one that now runs Tasmota, ESPHome or
OpenBeken, the tool says so and reads nothing.

Otherwise the tool downloads the offered firmware as the device would, except
any file the website already has, sending nothing to the device. For a
device the tool has not seen before, that is the whole readout.

A device listed as **Supported**, **Testers wanted** or **Readout wanted**
([devices](../devices/README.md)) is then read out in full. A variant of a
listed device that runs other firmware, such as its Matter version, counts as
one the tool has not seen. The tool says what it will do, and sends nothing
to the device until you type PROCEED.

### Every device at once

With two or more devices in your account, the first choice (`--all`
beforehand) is to read out every one, including those the tool converts: one
may run a firmware version the website has no readout of, and new firmware is
where a conversion most likely meets something it is not ready for.

After one question about a way to reach you, each device is read out in turn,
the quick readouts first, each sent as soon as it is ready. At a device's
PROCEED, type s to skip just that device, or q to quit, as at any question.

At the end the tool says what became of each device: sent, not needed (one
like it is in already), or not read out because the website has taken all it
can for today, takes 20 readouts a day from one place, or can take no more
for a few weeks. Each readout gets its own puzzle ([Sending it](#sending-it)).
If you added your email address, you are told when support for any of these
devices changes, whether or not its readout was needed.

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
  the restarted device shows online in eWeLink. This is proven in the
  emulator, not yet on a real Beken device.
- **Any other chip**: nothing is sent to the device. The readout is the
  firmware eWeLink offers it, and what the device and eWeLink say it is.

Whichever it is, the device keeps its Sonoff firmware, its settings and its
eWeLink pairing.

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
| `manifest.json` | what eWeLink and the device said they are (model, firmware version, chip), the tool's version, how many things were taken out, and your contact if you gave one | yes | yes |
| `flash.bin.xz` | the whole memory, anonymised, compressed | yes | yes |
| `efuse.bin.xz` | the chip's fixed settings (its eFuses), as the reading program saw them, compressed | yes | |
| `info.txt` | what the reading program said about the device | yes | yes |
| `report.json` | the check of the memory read: its partitions and hashes | yes | |
| `offered/<name>` | each file of the firmware eWeLink offers the device, as eWeLink serves it | yes | yes |

A device whose memory was not read has only the manifest and the offered
firmware.

## Sending it

The tool shows what goes ("the device's firmware readout, anonymised") and
asks you to add an email address, to hear when support for the device
changes, send it without one, or quit and keep it unsent.

Before it sends, your computer works out a small puzzle, which lets the
server turn away floods without a password. It usually takes 10 to 30
seconds, sometimes a minute or two, by luck and your computer's speed, while
the tool shows it is still working. If the website is busy
with another readout from you, the tool tries again every 15 seconds, for up
to a minute and a half.

Once it is sent, the tool gives you the readout's reference to quote if you
post about the device.

A readout you did not send, or that could not be sent, stays in the run's
folder as `readout.tar` (`readout-<device id>.tar` when every device was read
out at once). To send it later, run:

```sh
sh start.sh readout --send <path to readout.tar>
```

## Where it goes

The readout goes to the project's server at `api.firmwareliberator.com`,
which stores it privately, never publishes it, and uses it only to add
support for the device. It is kept for good, since a readout of one firmware
version can help with the device's later versions too. Nothing about you is
stored with it but the contact information you chose to give. Cloudflare,
which runs the server, logs each request with your internet address for
seven days, so that the maintainer can look into a failure.

Past the server's daily limit, yours stays unsent on your computer, to send
the next day. One of a device the project has enough readouts of already is
thanked for and not stored.

### Your email address

If you give your email address, you are sent an email with a link to confirm
it, and once more the next day if you have not. Nothing is kept unless you
follow that link: an address never confirmed is deleted after 7 days.

Once confirmed, the address is stored privately with your device's model,
apart from the readout, and used only to email you when that model's support
changes, such as when it becomes supported or a test version is ready to try.
Every such email has a link to remove your address, which asks you to confirm
first.
