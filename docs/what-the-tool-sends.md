# What the tool sends

Each time you run a conversion, the tool sends the project a short report of
how it went. Nothing in it says who you are, where you are, or which network
you are on.

Together, reports show which devices convert well, which fail and where, and
which people want supported next: see the
**[stats page](https://firmwareliberator.com/stats)**.

## What a report holds

- **How the run ended:** converted, updated, put back on the firmware the
  device came from, or stopped; if stopped, at which stage, the kind of
  problem (such as "the device stopped answering"), and the tool's message
  about it, with any addresses taken out.
- **The device:** its kind and model (such as Sonoff S60, S60TPF), and the
  version of Sonoff's firmware it had.
- **The conversion:** the firmware it went from and to, whether a backup of
  Sonoff's firmware was taken, whether the power meter kept its calibration,
  and, going back to Sonoff's firmware, where the eWeLink identity came from
  (the backup, the device or your account), never the identity itself.
- **How the run went:** how long it and each stage took, how long each stage
  kept the device busy, how often a stage was tried again, the device's Wi-Fi
  signal strength, and whether the run carried on an earlier one, you typed
  your Wi-Fi details in (not the details themselves), your computer joined the
  device's own setup network, the device had to be switched off and on, and
  its relay switched in the check at the end.
- **The tool and your computer:** the tool's version, your operating system
  and its version (such as Windows 11 or Ubuntu 24.04), and the Python
  version the tool ran on, its own or one already on your computer.
- **For counting:** a random number, so a report sent twice counts once, and
  a mark telling a maintainer's test run from everyone else's.

## What a report never holds

- Your name, email address or eWeLink account.
- Your Wi-Fi network's name or password, or any other password.
- Any address: your computer's, your router's, or the device's own or its
  hardware (MAC) address.
- The name you gave the device, your computer's name, or where you are.
- The time of day of the run: only the date the report arrived is kept.

## How your device is recognised

So that a device converted twice counts as one, the tool turns its hardware
address into a code on your computer with a one-way hash, and sends only that.
The server hashes it again with a secret key that never leaves it. What is
stored cannot be turned back into your device's address, and is linked to
nothing about you.

## Where it goes, and how long it is kept

The report goes to the project's server, `api.firmwareliberator.com`, on
Cloudflare. Like any website, it sees your internet address, and uses it only
to limit how many reports one computer can send: it holds the address in
memory for a minute, and counts it for the day as a scrambled code deleted
after a week. The address itself is never stored.

- **Each report** is deleted after a year, leaving only totals made from it,
  such as "so many S60s converted in November".
- **Each device's record**, so it is still counted once, is kept for as long
  as the project runs: when it was first seen, how its first run ended, and
  how many times it has been converted.

## What it is used for

- **The stats page**, which shows totals only, never a figure standing for
  fewer than five reports or devices, so no one report can be picked out. The
  figures are published under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), free for anyone to
  use with credit.
- **Finding what to fix:** where runs stop, on which devices.
- **Choosing which devices to support next**, from how often each is seen.

Reports are never sold, shared, or used to contact you.

## Turning it off

- **For one run:** add `--no-report`, as in `sh start.sh run --no-report`
  (`start.cmd run --no-report` on Windows).
- **For every run:** add `AUTOFLASH_NO_REPORT=1` to the `.env` file in the
  tool's folder.

On its first run on a computer, the tool says it sends reports, linking to
this page. A dry run (`--dry-run`) and a run in the
[emulator](emulator.md) never send one.

## A report that could not be sent

If the report cannot be sent when a run ends (your computer offline, or the
server silent), it waits in `captures/reports/` in the tool's folder
and is sent the next time you run the tool. It is deleted after 30 days if
still unsent, and only the newest 99 are kept. To delete one before it is
sent, delete its file there.

## Readouts are separate

A [readout](readouts.md), which helps add support for a device, is sent only
if you agree when the tool offers one; [its own page](readouts.md#what-is-in-one)
says what it holds.
