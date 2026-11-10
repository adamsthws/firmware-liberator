# What the tool sends

Each time you run a conversion, the tool sends the project a short report of
how it went: which device it was, which firmware it went to, and whether it
worked or where it stopped. Nothing in it says who you are, where you are, or
which network you are on.

Reports from everyone show which devices convert well, which fail and where,
and which devices people want supported next. You can see what they add up
to on the **[stats page](https://firmwareliberator.com/stats)**.

## What a report holds

- **How the run ended:** converted, updated, or stopped. For a run that
  stopped, the stage it stopped at, the kind of problem (such as "the device
  stopped answering"), and the tool's own message about it, with any
  addresses taken out.
- **The device:** its kind and model (such as Sonoff S60, S60TPF), and the
  version of Sonoff's firmware it had.
- **The conversion:** the firmware it went from and to, whether a backup of
  Sonoff's firmware was taken, and whether the device's power meter kept its
  calibration.
- **How the run went:** how long it took and how long each stage took, how
  many times a stage was tried again, how strong the device's Wi-Fi signal
  was, whether you typed your Wi-Fi details in (not the details themselves),
  whether your computer joined the device's own setup network, and whether
  its relay switched in the check at the end.
- **The tool and your computer:** the tool's version, your operating system
  and its version (such as Windows 11 or Ubuntu 24.04), and the version of
  Python the tool ran on, and whether it was the tool's own copy of Python or
  one already on your computer.

## What a report never holds

- Your name, email address or eWeLink account.
- Your Wi-Fi network's name or password, or any other password.
- Any address: not your computer's, your router's or the device's, nor the
  device's hardware (MAC) address.
- The name you gave the device, your computer's name, or where you are.
- The time of day the run happened. Only the date the report arrived is
  kept.

## How your device is recognised

So that a device converted twice counts as one, the tool turns your device's
hardware address into a code on your computer, using a one-way hash, and sends
only that code. The project's server then hashes that code again, with a
secret key that never leaves the server. What is stored cannot be turned back
into your device's address, and it is not linked to anything about you.

## Where it goes, and how long it is kept

The report goes to the project's server, `api.firmwareliberator.com`, which
runs on Cloudflare. Like any website, the server sees your internet address
when the report arrives. It uses it only to limit how many reports one
computer can send: held in memory for a minute, and counted for the day as a
scrambled code that is deleted after a week. The address itself is never
stored.

- **Each report** is kept for one year, then deleted. Only totals made from
  it, such as "so many S60s converted in November", are kept after that.
- **Each device's record** is kept for as long as the project runs, so it is
  still counted once: when it was first seen, how its first run ended, and how
  many times it has been converted.

## What it is used for

- **The stats page**, which shows totals only. A figure that stands for fewer
  than five reports or devices is never shown, so no one report can be picked
  out. The figures are published under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), free for anyone to
  use with credit.
- **Finding what to fix:** the stages where runs stop, on which devices.
- **Choosing which devices to support next**, from how often each one is
  seen.

Reports are never sold, never shared with anyone else, and never used to
contact you.

## Turning it off

- **For one run:** add `--no-report`, as in `sh start.sh run --no-report`
  (`start.cmd run --no-report` on Windows).
- **For every run:** add `AUTOFLASH_NO_REPORT=1` to the `.env` file in the
  tool's folder.

The first time the tool runs on a computer, it says once that it sends
reports, with a link to this page.

## A report that could not be sent

If your computer was offline when a run ended, or the project's server did
not answer, the report is kept on your computer, in `captures/reports/` in the
tool's folder, and sent the next time you run the tool. It is deleted after
30 days if it still has not been sent. To delete a report before it is sent,
delete its file there.

## Readouts are separate

A [readout](readouts.md) of a device the tool does not support yet is
something else: it is only sent if you agree to it when the tool offers one,
and [its own page](readouts.md) says what it holds.
