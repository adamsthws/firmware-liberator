# How readouts work, and where they are kept

This page is for the project's maintainer. What an owner sees and does when
making a readout is in [docs/readouts.md](../docs/readouts.md); this page
covers how the tool makes one, its format, and the API and store that receive
it.

## How the tool makes one

[readout.py](../tools/autoflash/readout.py) runs it, reading an Espressif
device with [readout_espressif.py](../tools/autoflash/readout_espressif.py) and
a Beken one with [readout_beken.py](../tools/autoflash/readout_beken.py). A run
offers it at its first stage when the eWeLink account holds no supported
device, and `readout` runs it for any device in the account (`--plug-ip` picks
one), or for every device in it the tool does not convert (`readout.run_all`,
offered first where there are two or more). Every readout starts the same way:

1. eWeLink is asked what the device runs and which firmware it would be
   offered. A device that can have a second stage is found on the network by
   its eWeLink id first; any other is not looked for there at all, and asked of
   eWeLink by its id alone, so a device that answers no local discovery (a Matter
   one, say) is read out too. Where eWeLink's cloud has no update record for it,
   what the account's record says of it - its product model, firmware model and
   version - is the readout. The firmware name comes
   from that offer, else from the public files' names
   ([firmware_name.py](../tools/ewelink/firmware_name.py)), else, for a kind
   the tool converts, from its own list.
2. The firmware eWeLink would offer it, asked as one behind every release, is
   fetched whole as the device fetches it
   ([offered_firmware.py](../tools/ewelink/offered_firmware.py)). It is often
   another version than the one the device runs, so two to test against. A
   readout goes on without it.

Between the two, the tool asks the API whether a readout of that product
model is still wanted (`POST /wanted`, `readout.still_wanted`), with lighter
work of its own (below). One the API has enough of goes no further: nothing
is fetched, nothing is sent to the device, and no readout's work is done.
Where the API cannot say - unreachable, rate-limited, or an earlier Worker
without `/wanted` - the readout goes ahead, and `/readout` decides.

That much is the first stage, and only ever reads: nothing is sent to the
device. A device the tool has not seen gets that alone.

The second stage reads its whole memory, and writes a program to it to do so: a
bridge, or a readout OpenBeken. So it is only for a device the tool lists
([devices/](../devices/README.md)) whose status is **Supported**, **Testers
wanted** or **Readout wanted** - each proven, in the emulator at least, to start
that program and go back to its Sonoff firmware - and only under the firmware name
that kind's own firmware has: a variant sharing its product model, a Matter
version say, is a device the tool has not seen
([readout.whole_readout](../tools/autoflash/readout.py)). The BASICR4 is why: its
partition table is where earlier bridges did not look, and a bridge sent to it
would have stopped there and left it unable to start until it was opened. A device
the tool has not seen moves on to the second stage once its first readout is
understood and its readout proven in the emulator.

For the second stage the device's chip decides how its memory is read, and the
owner types PROCEED before anything is sent to the device.

Reading out every device, the contact question is asked once, and each
device's readout is made and POSTed on its own, so the API can answer
`enough` for one it has enough of (`ENOUGH`) and store the rest. The
first-stage ones go first. Each device gets the readout it would get alone.
The run's record is cleared of the last device's address, MAC, bridge and
progress before the next. The run is not filed under any device, but each one
written to is held, as a conversion holds it. A readout not sent is kept as
`readout-<device id>.tar` beside the run's log. A device passed by at
PROCEED (`s`), a first stage that fails or a send that fails is noted and the
batch goes on. `q` there stops it, saying what was sent and what was not, and
so does a full readout that fails part way, as that device may be left on its
reading program. Each device's readout carries its own proof of work, which
is good for that readout's bytes alone, so a batch costs its sender the work
once per device, as that many runs would.

Every readout of a whole memory logs what Sonoff's own firmware says of the
hardware it drives ([firmware_findings.py](../tools/ewelink/firmware_findings.py)):
the meter or sensor chips it names, a temperature watch like the BASICR4's, an
ADC it reads, external RAM, Matter, which of Sonoff's SDKs it is, and the
version of each ais record its settings hold. Each is a lead to check, not a
fact: the firmware can drive it, the board may not have it.

### An Espressif device (ESP32-C3, classic ESP32)

The chip comes from the firmware name's last part (`ESP32C3FN4`,
`ESP32D0WDV3`).

1. With PROCEED, the stock updater is sent the bridge, wrapped under the
   device's firmware name. The sender takes a model no kind has only with
   `--readout-model`, and then only for the exact bridge `firmware.json` pins
   for that chip ([serve_tasmota_ota.py](../tools/espressif/serve_tasmota_ota.py)).
   The bridge only reads, and at start-up it chooses Sonoff's slot for the
   next start. On a device whose partition table is at 0xC000 (the BASICR4's),
   bridge 4.4.6 and later take the table from there without writing it
   anywhere; an earlier one found none and stopped, on the device, at every
   start.
2. The whole flash is read through the bridge, checked against its own hash,
   and kept, untouched, as the device's Sonoff backup on the owner's computer.
   The owner then switches the device off and on, and the tool waits for
   Sonoff's firmware to answer.
3. An anonymised copy is made to send ([scrub.py](../tools/espressif/scrub.py)):
   - The Wi-Fi network's name and every Wi-Fi password the settings show are
     taken out: ESP-IDF's and Sonoff's (on Sonoff's newer "ais" SDK, the
     BASICR4's and MINIR4's, its pair record's), the old entries NVS leaves behind,
     every other copy of the same bytes, and each password's PMK. A network
     name shorter than four characters is taken out where the settings name
     it, but not searched for elsewhere, as unrelated bytes hold it too. The
     device's own setup network's name stays, its device id made up. Where a
     firmware keeps a password somewhere else, it goes as it is: the copy is
     private, and a question about it would only make the owner hesitate to
     send it.
   - The device's eWeLink identity (`deviceid`, `apikey`, `encrypt_key`,
     `devicekey` in Sonoff's settings; `deviceid` and `factory_apikey` in the ais
     SDK's factory record, whose checksum is made good) is replaced everywhere by made-up values
     of the same shape: a device id stays ten digits beginning 1000.
   - The firmware images are left byte for byte: they are Sonoff's published
     code, and a password found there is the firmware's own setup network's.
   - Every settings entry changed has its checksums made good again.

### A Beken device (BK7238)

The chip comes from the stock model the account reports (`SN-BK...`), so a
Beken device takes this path even though it does no LAN Control - it is found
and asked of eWeLink by its account id, not by a local advert.

1. The file eWeLink offers the device is unwrapped to its stock firmware and
   its partition table. Nothing is written unless that table is the 4 MB
   BK7238 one the readout OpenBeken is built for.
2. With PROCEED, the stock updater is sent the readout OpenBeken
   (`artifacts/openbeken-readout`), wrapped under the device's firmware name:
   a build that sets no pins, so it drives nothing on unmapped hardware. It
   joins the device's Wi-Fi from the stock store.
3. The whole 4 MB flash is read through OpenBeken (`/api/flash`, paced and
   retried), and kept on the computer as the device's own-data backup.
4. OpenBeken fetches the device's own Sonoff firmware back - the offered file's
   RBL, which its `AutoflashRevertToStock` writes to the stock download slot
   only after checking the slot is the stock bootloader's - and the bootloader
   applies it on the restart that follows. The run waits for Sonoff to show
   online in eWeLink again.
5. The flash is anonymised ([scrub.py](../tools/beken/scrub.py)) - the
   network's name and password, plain (`wifi-ssid`, `wifi-pass`) and encoded
   (`ssid`, `pass`), and every other copy, OpenBeken's included, as well as
   the identity (`apiKey`, `app-apikey`, `secureCertSha256`, `user`) - and
   sent as the others are. The `factory` record is kept as it is: the
   device's own factory credentials, encrypted, not its owner's, and the
   stock firmware does not start with it changed in any way (found by booting
   an S61 in the emulator with each setting taken out in turn).

Proven end to end against the emulated S61 (the whole round trip, the device
back online in eWeLink, the flash anonymised); it has not run on a real Beken
device.

### Sending it

Nothing is ever sent from the emulator or an unattended run.
`AUTOFLASH_READOUT_URL` points a test at a server of its own.

## Why an anonymised copy is enough

The emulator needs the flash as the device has it, not who the device is: its
eWeLink identity only matters to eWeLink's servers, which an emulated copy
never meets, as its cloud is the emulator's stub. The public test plugs are
made the same way ([public_dump.py](../tools/espressif/emulator/public_dump.py)),
and boot and convert in the emulator. A readout keeps all a public dump lacks:
the device's real partition table, its factory data, its calibration and its
own settings, which is what the devices not converted yet ([devices/](../devices/README.md)) stop for.

## Its format

A tar, the manifest first and as it is, so the API reads it without unpacking
anything; each `.bin` in it is xz-compressed (`flash.bin.xz`), about a quarter
smaller than gzip on a real dump (a 4 MB S60 flash: 1.0 MB). The files are
those [docs/readouts.md](../docs/readouts.md#what-is-in-one) lists.

`manifest.json` holds: format 1; the tool's version; eWeLink's product model,
brand and uiid; the stock firmware's model and version; its firmware name and
where that came from; its chip; whether the memory was read; how many places
had the network's name or a password blanked and an identity replaced;
`contact`, the owner's `email`, only if they gave one; and
`offered`, each offered file's version, size and SHA-256. An Espressif one adds
the facts the run logs (chip revision, security fuses, flash size, partition
table); a Beken one its partition table's name (`layout`) and what the readout
OpenBeken said of itself (`device`: its build and chipset).

On Espressif, `info.txt` is the bridge's `GET /info`, and `report.json` the
dump's check: partitions, which slot the boot selection picks, hashes. The
`offered/` files are as eWeLink sends them (`user1.bin`, `user2.bin`).

## Where it goes

`POST https://api.firmwareliberator.com/readout`: the project's API, a Worker
of its own ([api/index.js](../website/api/index.js)), apart from the website's,
so nothing sent to it can affect the site. The tool is open source, so it can
hold no secret to sign in with; instead every readout costs its sender work,
and the store has limits it is never let past:

- **Proof of work.** The tool finds a nonce so that SHA-256 of
  `<time>:<the readout's SHA-256>:<nonce>` starts with 24 zero bits: about 17
  million tries, 10 to 30 seconds on a computer, once a readout. Hardware
  made for hashing does it in milliseconds, so this only stops a cheap flood;
  the daily limits below are what bound the store. It goes in `X-Proof`, is
  good for that readout alone, and only within 15 minutes of when it was made:
  however many devices one run reads out, each is worked for on its own.
- **It must be one.** `application/x-tar`, at most 24 MB, its first file a
  `manifest.json` of the format the tool writes.
- **Asking first.** `POST /wanted` takes `{"format": 1, "product": "<product
  model>"}` and answers `{"wanted": true}`, or false for a model in `ENOUGH`.
  It reads nothing but `ENOUGH` and stores nothing, so its proof of work, made
  the same way over the question's own bytes, is lighter: 20 zero bits, about
  a million tries, a second or two on a computer (one in 16 of a readout's
  work), and good for a minute, not 15. Each
  question needs its own. It is held to the hostname's rate-limiting rule, but
  not to the two-readouts-a-minute limit, which a batch's questions would use
  up before its readouts.
- **Rate.** Before the Worker runs at all, a Cloudflare rate-limiting rule on
  its hostname (`tools/repo/cloudflare_settings.py`) blocks an address sending
  more than 5 requests in 10 seconds, for 10 seconds: a flood never reaches the
  Worker, where every request, even one turned away, counts against the
  account's 100,000 a day on Workers' free plan. Then two readouts a minute
  from one address, held by Cloudflare's rate limiter only. 100 readouts and
  250 MB a day from everyone; past that, the next waits until the next day.
- **Repeats.** The same readout sent again the same day is the one already
  stored.
- **Enough of one device.** A product model listed in `ENOUGH` in the Worker's
  [wrangler.jsonc](../website/api/wrangler.jsonc) is thanked and not stored.
- **Retention.** Each readout is stored privately in the R2 bucket
  `firmware-liberator-readouts` as `readouts/<date>/<hash>.tar`; its
  reference is `<date>-<12 hex>`. The bucket's lifecycle rule deletes
  everything under `readouts/` 30 days after it came in. 250 MB a day for 30
  days, 7.5 GB, plus 2 GB under `kept/`, stays within R2's free 10 GB.

Nothing of the sender is stored but the contact they chose to give, in the
manifest. Nothing serves the bucket.

The bucket, its lifecycle rule and the rate-limiting rule are made by
`python3 tools/repo/cloudflare_settings.py apply`, by hand, and checked weekly
by the cloudflare-settings workflow. The website workflow deploys the Worker
in a job of its own. [website/README.md](../website/README.md#deploying-it)
says which token does what.

## Working with them

```sh
python3 tools/repo/readouts.py
cd website
npx wrangler r2 object get firmware-liberator-readouts/readouts/2026-11-07/0a1b2c3d4e5f60718293a4b5.tar --remote --file readout.tar
```

Both take a token from `CLOUDFLARE_API_TOKEN` that can read the account's R2,
such as the settings check's; putting or deleting one needs one that can edit
it.

- **One to keep**, past its 30 days: put it under `kept/`
  (`wrangler r2 object put firmware-liberator-readouts/kept/<name> --file ...`),
  then delete the original. Keep `kept/` under 2 GB.
- **Duplicates.** The manifest's `flash.sha256` is of the device's own flash
  before it was anonymised, so two readouts of one device share it.
- **Once a device is supported**, add its product model to `ENOUGH`, and delete
  its readouts but the one kept.

Keep what you fetch under `captures/`: like any dump, it never goes into the
repository. [Adding a device](../docs/adding-a-device.md) takes it from there,
as a whole dump.
