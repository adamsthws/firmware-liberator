# What the tool sends

Each time you run a conversion, the tool sends the project a short report of
how it went; each device you [read out](readouts.md) sends one too, as its
readout ends. Nothing in it says who you are, where you are, or which network
you are on.

Together, reports show which devices convert well, which fail and where, the
stages people choose to stop at, and which devices people want supported next: see the
**[stats page](https://firmwareliberator.com/stats)**.

## What a report NEVER holds

- Your name, email address or eWeLink account.
- Your Wi-Fi network's name or password, or any other password.
- Any address: your computer's, your router's, or the device's own or its
  hardware (MAC) address.
- The name you gave the device, your computer's name, or where you are.
- The time of day of the run: only the date the report arrived is kept.

## What a report holds

- **How the run ended:** converted, updated, put back on the firmware the
  device came from, or stopped; if stopped, at which stage, the kind of
  problem (such as "the device stopped answering"), and the tool's message
  about it, with any addresses taken out. If you stop it yourself, the stage
  you stopped at and how: q at a question, Ctrl-C, or closing the window,
  and if you stopped at a question, which one, as a word (such as
  `which-account` or `go-ahead`), never its words or your answer.
  Nothing else is asked of you, and a run you stop before its first stage
  sends no report. If the tool itself went wrong, the place in its own code
  where it did (such as `KeyError at tools/autoflash/report.py:212`), never
  any of your values.
- **The device:** its kind and model (such as Sonoff S60, S60TPF), as far as
  the run knew them - none for a run stopped before it found a device, at the
  eWeLink sign-in say, and the kind alone for an update of a converted device,
  which asks eWeLink nothing - the
  version of Sonoff's firmware it had, the name its own updater checks an
  update against (and whether it is the one the tool expects), and on an
  Espressif device its chip's revision and its flash chip's maker code and size.
  If the tool turned down the device's partition table (its memory not laid
  out as the tool expects), or the one in Sonoff's update file for it, a
  hash of that table and where it was read, so the project hears of it: a
  table is the same on every device of a model, so it says nothing of yours.
- **The conversion:** the firmware it went from and to, whether a backup of
  Sonoff's firmware was taken, whether the power meter kept its calibration,
  and, going back to Sonoff's firmware, where the eWeLink identity came from
  (the backup, the device or your account), never the identity itself.
  Where the new firmware Sonoff's firmware sent did not answer, what the
  tool found when it checked whether Sonoff's firmware still did, before
  asking you to switch the device off and on, as a word: `sonoff-answers`
  (it was not installed), `neither-answers`, or `cannot-tell`.
- **Whether the device's eWeLink identity could be rebuilt from your
  account** ([below](#the-identity-check)): for each of a fixed list of
  fields, yes, no, missing or unknown. Never the identity itself.
- **A readout's report** (one for each device read out): how it ended -
  accepted by the website, kept on your computer unsent, not needed (one like
  it is in already), turned away as the website was full, not read as the
  device now runs other firmware, skipped, stopped by you, or failed, with
  the kind of problem; one stopped or failed part way says the stage it was
  in, and the question you stopped it at, and what a check of Sonoff's
  firmware found, as a conversion's does, and the
  [identity check](#the-identity-check)'s outcomes where its memory was
  read - the device's kind, its model and
  the name and version
  of its Sonoff firmware, whether its whole memory was read, and how long it
  took. Never the readout itself, nor its reference.
- **Before a readout, the question whether one is wanted:** the device's
  model and the name and version of its Sonoff firmware, and the device as a
  report names it, so the website counts how many devices of each model
  people would read out, not how many times they asked.
- **How the run went:** how long it and each stage took, how long each stage
  waited for your answers, how long each stage kept the device busy, how
  often a stage was tried again, the device's Wi-Fi signal strength, and
  whether the run carried on an earlier one (and the tool's version that
  began it, if another), you typed your Wi-Fi details in (not the details
  themselves), your computer joined a network the device raised itself, the
  device had to be switched off and on (and how long it took to answer once
  back on) - each also by stage, as neither should be needed, and for the
  device's own network why: it was never given your Wi-Fi details, was given
  them but did not join, or had lost its settings - and whether the checks at the end passed (its relay, its
  firmware's release, its sensors' readings). Also how often
  you pressed Ctrl-C and then carried on.
- **The tool and your computer:** the tool's version, how you started it
  (`start.sh` or `start.cmd`) and whether from a download or a git clone,
  your operating system, its version (such as Windows 11 or Ubuntu 24.04) and
  your processor's kind (x86-64 or ARM), and the Python version the tool ran
  on, its own or one already on your computer. What may have got in the way
  on your computer's side: whether it reaches the device's network over Wi-Fi
  or Ethernet, whether a VPN was on, how many networks it is on, whether the
  tool added a firewall rule or asked for administrator rights, and whether
  it pointed out other security software (never which). The region of your
  eWeLink account (Europe, the Americas, Asia or China), if the run signed in to it. For a
  conversion, how many devices the tool has converted on your computer, this
  one included; for a readout, how many it has converted there so far:
  counted from its own records there, so deleting them starts the count
  again. Nothing that names your computer is sent.
- **The offers sent beside the run** ([below](#what-ewelink-offers-your-devices)),
  in counts and yes-or-no answers alone: whether they were sent, and if not,
  why, in a word (such as reports turned off, an unattended run, all sent
  recently, or no sign-in held); how many devices were due, how many
  questions eWeLink was asked, how many offers were sent and how the
  project answered them, how many files went by a link for review and how
  many devices were left out for a link unfit for it; whether eWeLink ended
  the sign-in while they were asked for, and whether the run's own sign-in
  was ended after. If the run ends first, what was done by then. Never
  which devices, nor anything of your account.
- **For counting:** a random number, so a report sent twice counts once, and
  a mark telling a maintainer's test run from everyone else's. The number is
  the report's ID: when a run fails, the tool shows it where it says to open
  an issue, and puts it with the report on your clipboard to paste in, so the
  issue names its report. An issue is public: one that names a report lets
  anyone see that report's details beside it.

## The identity check

Going back to Sonoff's firmware without a backup, the tool rebuilds the
device's eWeLink identity (its device ID and key, its model and kind) from
your eWeLink account. Whether that rebuild is the device's own on every
model is what the identity check finds out. Where the tool holds both the
device's memory and your signed-in account - a readout of an Espressif
device, or a conversion's backup of one taken while signed in - it compares
the two on your computer, before anything is taken out of a readout, and
sends only the outcomes.

- **Each outcome is a word:** `yes` (the device's value is the rebuilt one),
  `no` (it is not, or the device has a value the rebuild leaves out),
  `missing` (the device has none) or `unknown` (it cannot be told, as when
  your account's record lacks the key). Beside them, a word for where the
  device's firmware keeps its identity: `esf` (the S60's and the classic
  ESP32 devices'), `ais` (the BASICR4's and MINIR4's) or `neither`.
- **The fields are a fixed list**, `ESF_FIELDS` and `AIS_FIELDS` in
  [`tools/espressif/identity_check.py`](../tools/espressif/identity_check.py):
  the device ID, key, model and kind, and the other fields the tool rebuilds
  or leaves out; on the BASICR4 and MINIR4 also whether their identity's
  checksum is worked out from its fields, whether the hardware addresses it
  holds are the chip's own, and whether the whole record rebuilt is the
  device's. Each is compared, never sent.
- **Never sent:** any value compared, a hash or the length of one, your
  account or anything of it. The outcomes go in the readout's
  `manifest.json` and its report, or in the run's report; the run's log on
  your computer holds the outcomes too, and no value either.

## What eWeLink offers your devices

Once a conversion or a readout has signed in to your eWeLink account, the tool
also asks eWeLink, in the background, which firmware it offers each device in
the account, and sends the project that offer, so the project has the
firmware devices are given, to test the tool against. An offer holds, for each
device, its model and the name and version of its Sonoff firmware, and for
each file eWeLink offers, its name, version, SHA-256 and path on eWeLink's
server. Nothing in it says which device it is. The project then fetches those
files from eWeLink itself.

- **An offer never holds** your eWeLink account or its sign-in, a device's
  eWeLink ID, its name, or any address of yours. The path is the same for
  every device on that release: the tool leaves out the rest of the link.
- **A link of a shape the tool has not seen before** is sent whole instead,
  for the project to check before it fetches anything from it: eWeLink's
  server and the path, without the part after `?` or `#`. It is sent only if
  it is plain (an eWeLink file server or a public address, and a path of
  letters, digits and `. _ - /` alone) and never if it holds anything the
  tool knows of the device or your account, in any form: the device's
  eWeLink ID, its keys, its MAC address or the next few either side of it
  (which the device uses too), or your account's key or sign-in. A readout you
  send still carries the file itself beside such a link
  ([readouts](readouts.md#a-link-of-another-shape)).
- **It is sent unasked**, as a report is, and only while the website asks for
  links. Nothing of it shows on your screen and the run never waits for it:
  the small puzzle it carries, which lets the server turn away floods, is
  worked out beside the run at the lowest priority. It uses the run's own
  sign-in to eWeLink, never one of its own. If the run ends first, it is
  dropped: nothing is kept to send later. A device whose offer has a file
  with a link of another shape that cannot be sent is left out, to go once a
  later version knows it.
- **Each device's offer goes at most once a week** from your computer
  (`OFFER_RESEND_DAYS` in
  [`tools/autoflash/offers.py`](../tools/autoflash/offers.py)): the day it
  last went is kept in `captures/offers-sent.json`, by a code made from the
  device's address with a secret kept there, never its ID or address: the
  code leads back to no device without that secret, which never leaves your
  computer.
- **It is turned off as reports are** ([below](#turning-it-off)), and a dry
  run, the emulator and an unattended run never send one.

## How your device is recognised

So that a device converted twice counts as one, and a readout of it and its
conversion are known as the same device, the tool turns its hardware address
into a code on your computer with a one-way hash, and sends only that. A
readout takes the address from your eWeLink account's record of the device,
or where that has none, looks for the device on your network to learn it; one
it cannot find is sent with no code.
The server hashes it again with a secret key that never leaves it. What is
stored cannot be turned back into your device's address, and is linked to
nothing about you.

## Where it goes, and how long it is kept

The report goes to the project's server, `api.firmwareliberator.com`, on
Cloudflare. Like any website, it sees your internet address. Cloudflare logs
each request and keeps a trace of it - where it was
handled, and how long
each step took - for seven days, so that the maintainer can look into a
failure; neither is used for anything else. The server
itself uses your address only to limit how many reports one computer can
send: it holds the address in memory for a minute, and counts it for the day
as a scrambled code deleted after a week. It counts your day's reports of
each kind of device the same way, under another such code that ties you to
nothing else, to tell many people converting a device on one day from one
computer sending made-up reports. So that a made-up model name cannot
be put on the public figures by one computer, it also notes, for each model a
report names, a scrambled code of your address and that model, which ties
you to nothing else; it is deleted once enough people have named that model,
or after a year. The server never stores the address itself.

- **Each report**, a conversion's or a readout's, is kept for as long as the
  project runs, so that how devices fare can be followed over the years.
  Nothing in it says who you are; it is the totals made from reports, never a
  report, that are published, such as "so many S60s converted in November".
- **Each device's record**, so it is still counted once, is kept for as long
  as the project runs: when it was first seen, how its first run ended, how
  many times it has been converted, and that count from the computer that
  first converted it.

## What it is used for

- **The public stats page**, which shows totals only, never a figure standing for
  fewer than five reports or devices, so no one report can be picked out. The
  figures are published under
  [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), free for anyone to
  use with credit.
- **Finding what to fix:** where runs stop, on which devices.
- **Choosing which devices to support next**, from how often each is seen.

Reports are never sold, shared, or used to contact you.

## Turning it off

These stop both reports and offers.

- **For one run:** add `--no-report`, as in `sh start.sh run --no-report`
  (`start.cmd run --no-report` on Windows).
- **For every run:** add `AUTOFLASH_NO_REPORT=1` to the `.env` file in the
  tool's folder.

On its first run on a computer, the tool says it sends reports and offers,
linking to this page. A dry run (`--dry-run`) and a run in the
[emulator](emulator.md) never send one.

## A report that could not be sent

If the report cannot be sent when a run ends, tried again a few seconds
later (your computer offline, or the server silent), it waits in
`captures/reports/` in the tool's folder
and is sent the next time you run the tool. It is deleted after 30 days if
still unsent, and only the newest 99 are kept. To delete one before it is
sent, delete its file there.

## Readouts are separate

A [readout](readouts.md), which helps add support for a device, is made only
when you choose one; [its own page](readouts.md#what-is-in-one) says what it
holds. The short report of how a readout went, above, is not the
readout: it is sent as a conversion's report is, and turned off the same way.

## Providing your email address (optional)

As it exits, the tool asks whether you would like to receive a notification as more devices
are supported. If your answer cannot be sent then, the tool keeps it in
`captures/settings.json` and sends it as it next exits, instead of asking
again. You can
also sign up on the website's [notify page](https://firmwareliberator.com/notify).

You are sent an email with a link to confirm your email address. An
address never confirmed is deleted after 7 days. Once confirmed, it is used only for notification emails. [Privacy](privacy.md) says who keeps it, and your rights.

**Unsubscribing**  
Once you remove your address,
or if you never confirmed it, nothing more is kept or sent to it; to sign up
again, use the [notify page](https://firmwareliberator.com/notify).

