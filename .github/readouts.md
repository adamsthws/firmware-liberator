# How readouts work, and where they are kept

This page is for the project's maintainer. What an owner sees and does when
making a readout is in [docs/readouts.md](../docs/readouts.md). This page
covers the design behind a readout, the API and store that receive it, and
how to look after what they hold. The step-by-step, and every limit with its
reason, is written beside the code that does it:

| For | Read |
|---|---|
| How a readout is made, for one device or every device in an account | [readout.py](../tools/autoflash/readout.py) |
| The second stage on an Espressif device, through the bridge | [readout_espressif.py](../tools/autoflash/readout_espressif.py) |
| The second stage on a Beken device, through the readout OpenBeken | [readout_beken.py](../tools/autoflash/readout_beken.py) |
| What is taken out of a copy | [tools/espressif/scrub.py](../tools/espressif/scrub.py), [tools/beken/scrub.py](../tools/beken/scrub.py) |
| What Sonoff's firmware says of the hardware it drives | [firmware_findings.py](../tools/ewelink/firmware_findings.py) |
| Each path of the API, what it takes and answers | [website/api/openapi.json](../website/api/openapi.json) |
| How each path is guarded | [website/api/index.js](../website/api/index.js) and the modules beside it |
| The bucket, the rules in front of the API, its database and its secrets | [cloudflare_settings.py](../tools/repo/cloudflare_settings.py), [wrangler.jsonc](../website/api/wrangler.jsonc) |

## Two stages

The first stage only ever reads. eWeLink is asked what the device runs and
which firmware it would be offered, and that offered firmware is fetched as
the device would fetch it. Nothing is sent to the device, so a device the
tool has never seen gets this stage alone, even one that answers no local
discovery.

The second stage reads the device's whole memory, and has to write a program
to it to do so: the bridge on an Espressif device, a readout OpenBeken on a
Beken one. It is therefore only for a device the tool lists
([devices/](../devices/README.md)) whose status is **Supported**, **Testers
wanted** or **Readout wanted**, each proven, in the emulator at least, to
start that program and go back to its Sonoff firmware. It is also only for
the firmware name that kind's own firmware has: a variant sharing its product
model, a Matter version say, counts as a device the tool has not seen. The
[BASICR4](../devices/sonoff-basicr4/README.md) is why. Its partition table is
where earlier bridges did not look, and a bridge sent to it would have
stopped there and left it unable to start until it was opened. A new device
moves on to the second stage once its first readout is understood and its
readout proven in the emulator. The owner types PROCEED before anything is
sent.

Afterwards an Espressif device is restarted into its Sonoff firmware by the
bridge, or switched off and on by its owner where the bridge cannot. A Beken
device has one app area, so the readout OpenBeken fetches the device's own
Sonoff firmware back. That path is proven end to end on the emulated S61 and
has not yet run on a real Beken device.

Before a readout is made, the tool asks the API whether it would add
anything. One that is not wanted goes no further: nothing is fetched and
nothing is sent to the device. Nothing is ever sent from the emulator or an
unattended run.

## What is taken out, and what is left

The copy kept on the owner's computer is the device's own, untouched: it is
its Sonoff backup. The copy that is sent is anonymised first.

- **Taken out:** the Wi-Fi network's name and every Wi-Fi password the
  settings show, with each older copy the settings store has left behind and
  every other copy of the same bytes; and the device's eWeLink identity,
  replaced everywhere by made-up values of the same shape.
- **Left:** the firmware itself, byte for byte, as it is Sonoff's published
  code. The device's own setup network's name stays, with its device id made
  up. On a Beken device the `factory` record stays, as it holds the device's
  own factory credentials, encrypted, and the stock firmware does not start
  with it changed in any way.
- **Not looked for:** a password that a firmware keeps somewhere its settings
  do not show. The copy is private, and a question about it would only make
  an owner hesitate to send it.

An anonymised copy is enough because the emulator needs the flash as the
device has it, not who the device is. Its eWeLink identity matters only to
eWeLink's servers, which an emulated copy never meets, as its cloud is the
emulator's stub. The public test devices are made the same way
([public_dump.py](../tools/espressif/emulator/public_dump.py)), and boot and
convert in the emulator. A readout keeps all that a public dump lacks: the
device's real partition table, its factory data, its calibration and its own
settings, which is what the devices not converted yet stop for.

## Its format

A readout is a tar with `manifest.json` first and uncompressed, so the API
reads the manifest without unpacking anything. Each `.bin` read from the
device is xz-compressed; the files eWeLink offers, under `offered/`, are not,
so the API checks each against the SHA-256 the manifest gives it, and turns
the readout away if one is missing or differs. Only a file checked so is
marked held (`readouts/firmware/`), so no readout can have the tool leave out
a file the store does not have. The files are those
[docs/readouts.md](../docs/readouts.md#what-is-in-one) lists, and
`readout.package` and the two `read_and_anonymise` functions write them.

The API relies on this much of the manifest: its `format`, its `product`,
`stock_model` and `version` (together the readout's key), whether the memory
was `read`, and what every genuine readout of that key shares - `firmware`
and `layout` for a whole one, the SHA-256 of each `offered` file for a first
stage - and each `offered` file's `file`, where it is in the tar. A change to any of these is a change to both sides.

## Where it goes

Readouts go to the project's API, a Worker apart from the website's. The
tool is open source, so it can hold no secret to sign in with. Instead each
request costs its sender some work, the store has hard limits it is never
let past, and only a readout that adds something is kept:

- A readout is kept only while fewer than three of its key are held, each
  from a different sender. Three that agree are taken as right; any other
  waits for review, in room set aside for it, and is deleted after a while if
  it is never settled. So made-up readouts can neither fill the store nor
  hold a key's places for long.
- A kept readout is kept for good, in a private bucket that nothing serves:
  an old one may help with a device's later versions.
- An owner may ask to be told when a device's status changes. The address is
  kept only once its owner confirms it from an email, and until they remove
  it with the link every such email carries.
- Nothing of a sender is stored but the contact they chose to give. Senders
  are counted under a keyed hash, never by address.

The confirmations and the nightly alerts are sent by Cloudflare Email
Service, from the domain `cloudflare_settings.py` onboards for sending.

## Bounces and complaints

A bounce or a spam complaint comes back after an email has gone, so the
Worker never sees it. Email Service suppresses the address itself. The
cloudflare-settings workflow's daily `health` job (`api_health.py mail`)
fails when too many emails bounced or on any complaint, because Email Service
pauses an account's sending, alerts included, once its reputation is poor.
When it fails:

1. Look at the dashboard's Email Sending activity log, which has each bounce
   and its reason, and the suppression list, which names the address. The job
   prints counts only, as CI's log is public.
2. Many bounces at once from mistyped or made-up addresses mean someone is
   giving them: lower `NOTIFY_SENDER_DAILY` or `NOTIFY_DAILY` until it stops.
3. After a complaint, remove that address from the list (its `/forget` link,
   or by hand under `notify/`). Leave it suppressed, so it is never written
   to again, unless its owner asks to be told once more.

## Rotating SENDER_KEY

Every count of a sender is a hash under `SENDER_KEY`, so a new key makes
each sender look new. `cloudflare_settings.py rotate SENDER_KEY` therefore:

- starts the day's counts of each sender again, for the rest of that day;
- lets a sender that already sent a readout of a key send another of it,
  taking a second of the key's places, so a key with readouts from both sides
  of a rotation can reach its quorum with two senders, not three;
- loses nothing: readouts and their reviews stay as they are, and no address
  was ever kept to link the old hashes to the new.

So rotate it only when it may have leaked, not on a schedule. It never
leaves the Worker's secrets, so a leak means someone reached the Cloudflare
account. The command prints the time it rotated and the command that lists
the keys taken as right from readouts sent on both sides of it
(`readouts.py rotated <time>`). Over the next weeks, look at each, and
`reject` a readout where two of the three could be the same sender's.

## Working with them

```sh
python3 tools/repo/readouts.py
cd website
npx wrangler r2 object get firmware-liberator-readouts/readouts/2026-11-07/0a1b2c3d4e5f60718293a4b5.tar --remote --file readout.tar
```

Both take a token from `CLOUDFLARE_API_TOKEN` that can read the account's R2,
such as the settings check's; putting or deleting an object needs one that
can edit it. `readouts.py --help` lists its other commands.

- **Reviewing.** `readouts.py review` lists the readouts waiting, by key,
  with an odd one's fingerprint marked; `approve ID` or `reject ID` settles
  one. A rejected one frees its place for another sender's.
- **Duplicates.** The manifest's `flash.sha256` is of the device's own flash
  before it was anonymised, so two readouts of one device share it.
- **Once a device is supported**, add its product model to `ENOUGH` in the
  API's `wrangler.jsonc`, so no more of it are taken. Its readouts stay.
- **Making room**, should the store fill (the nightly alert and the usage
  check warn long before): delete readouts no longer needed, each with its
  row in the `readouts` table and its offered files' `readouts/firmware/...`
  marks. Then take their bytes off the store's count, from `website/`:

  ```sh
  npx wrangler d1 execute firmware-liberator-api --config api/wrangler.jsonc --remote \
      --command "UPDATE counters SET count = count - <n>, bytes = bytes - <bytes> WHERE name = 'readouts:all'"
  ```

- **When a release changes a device's status**, write to everyone who asked
  to be told about it.
  1. Run `python3 tools/repo/readouts.py changes` on your own computer, never
     in CI, whose logs are public. It lists each device whose status differs
     from the latest release's, with each address given for it and its
     removal link (`readouts.py notify <PRODUCT>` lists one product's).
  2. Send each person their own message, never one with every address in
     view, with their own removal link in it (and as its `List-Unsubscribe`
     header, where the sender allows).
  3. Say why they are hearing from you: an address may have been given by
     someone other than its owner.

  An address stays on the list after it is written to, for the device's next
  change, until its owner removes it.

Keep what you fetch under `captures/`: like any dump, it never goes into the
repository. [Adding a device](../docs/adding-a-device.md) takes it from
there, as a whole dump.
