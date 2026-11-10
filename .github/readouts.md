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

Between the two, the tool asks the API whether the readout would add
anything (`POST /wanted`, `readout.ask_wanted`), with lighter work of its own
(below): its product model, firmware model and version, the stage it would be
(`first`, or `whole` where a second stage may follow), and the SHA-256 eWeLink
gives each file it offers. The API keeps three readouts of each model and
version (`PER_KEY`), each from a different sender, a whole one taking a first
stage's place too: one sender's ten identical plugs give one readout, alone
or in a batch, and three that agree come from three people. One not wanted goes no further:
nothing is fetched, nothing is sent to the device, and no readout's work is
done; the owner is offered, still, to leave an address to be told when its
support changes (`/notify`, below). A file the API holds already is not
fetched and goes in the manifest's `offered` as `"held": true` - but for a
Beken device read in full, which needs Sonoff's file to put back, and fetches
it all the same without sending it. Where the API cannot say - unreachable,
rate-limited, or an earlier Worker without `/wanted` - the readout goes ahead,
and `/readout` decides by the same rule.

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

Reading out every device, the contact question is asked once, and an address
given goes to `/notify` at once for every device in the batch, wanted or not.
Each device's readout is made and POSTed on its own, so the API can answer
`enough` for one it holds one like already, or has enough of (`ENOUGH`), and
store the rest. When `/wanted` says the day is full, the batch ends there. The
first-stage ones go first. Each device gets the readout it would get alone.
The run's record is cleared of the last device's address, MAC, bridge and
progress before the next. The run is not filed under any device, but each one
written to is held, as a conversion holds it. A readout not sent is kept as
`readout-<device id>.tar` beside the run's log. A device passed by at
PROCEED (`s`), a first stage that fails or a send that fails is noted and the
batch goes on. `q` there stops it, saying what was sent and what was not, and
so does a full readout that fails part way, as that device may be left on its
reading program. The API takes two readouts a minute from one address, which
a batch of quick first stages reaches (seen in the emulator): a readout
turned away with 429 is sent again every 15 seconds, for up to a minute and a
half, with the same proof of work. Every wait outside a stage - the puzzle,
these tries, the memory's read, Sonoff's return - shows the stages' spinner
(`output.working`), as nothing else on the screen moves. Each device's readout carries its own proof of work, which
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
  however many devices one run reads out, each is worked for on its own. A
  light proof (`/wanted`, `/notify`) is taken once: one sent again within its
  minute is refused (a day's count a proof, `proof:<hash>`), and the tool puts
  a random `nonce` in each question so two alike never share one.
- **It must be one.** `application/x-tar`, at most 24 MB, its first file a
  `manifest.json` of the format the tool writes.
- **Only what adds something, from different people.** A readout is stored
  only if fewer than three of its product model, firmware model and version
  (its key) are held - a whole one takes a first stage's place too, as it
  holds all one does - and its sender has none of that key; otherwise it is
  thanked (`enough`) and not stored, before it takes any of the sender's 20 a
  day. Each kept one is a row of the database's `readouts` table
  (migrations/0004_readouts.sql): its key and stage; its sender, as an HMAC
  of the key and the address under `SENDER_KEY`, so nothing ties one
  sender's readouts of different keys together; its fingerprint; and its
  review. The last place is taken in one statement, so two sent at once
  cannot both have it. Each offered file stored is marked as
  `readouts/firmware/<sha256>`.
- **Agreeing, and review.** A readout's fingerprint is a hash of what every
  genuine readout of its key shares, never its owner's settings: for a whole
  one, its firmware as the tool read it before anything was taken out
  (`manifest.firmware`: an Espressif device's partition table and each Sonoff
  app's project, version and build hash, `esp_app_desc_t`'s `app_elf_sha256`;
  a Beken device's layout and the Sonoff firmware it was put back on); for a
  first stage, the SHA-256 of each file eWeLink offers it. Its size, or its
  whole file's hash, says nothing: each holds its owner's own settings. Three
  of one stage that share a fingerprint are marked `quorum`, taken as right;
  every other readout is `pending`, waiting for review:
  `python3 tools/repo/readouts.py review` lists them by key, an odd one's
  fingerprint marked, and `readouts.py approve ID` or `reject ID` settles one.
  A rejected one frees its place for another sender's.
- **Asking first.** `POST /wanted` takes `{"format": 1, "product", "model",
  "version", "stage": "first"|"whole", "firmware": [<sha256>, ...]}` (8 files
  at most) and answers `{"wanted": bool, "why": "enough"|"held"|"full"|
  "store-full"|"limit", "held": [<the files held>]}` - "limit" once its
  sender has had 20 kept today. It reads only marks and the day's count, so
  its proof of work, made the same way over the question's own bytes, is
  lighter: 20 zero bits, about a million tries, a second or two on a computer
  (one in 16 of a readout's work), and good for a minute, not 15. Each
  question needs its own. It is held to the hostname's rate-limiting rule, but
  not to the two-readouts-a-minute limit, which a batch's questions would use
  up before its readouts.
- **Asking to be told, confirmed by its owner.** `POST /notify` takes
  `{"format": 1, "email", "devices": [{"product", "model"}, ...]}` (20 at
  most), with the same light work, and keeps nothing yet: the address is
  emailed a link to confirm it (`/confirm?t=<token>`, from `NOTIFY_FROM`),
  and the request waits in the database's `confirmations` table, deleted by the
  nightly job after 7 days unless confirmed. A day after its link was sent,
  the Worker's hourly cron sends it once more (`remind`,
  `REMIND_AFTER_DAYS`): once an address however often it was given, none
  already confirmed, counted against the 500 a day; its counter
  (`notify-remind:<hash>`) outlives the sign-up, so a run twice reminds
  nobody twice. It answers 202 whether or not an
  email went, so nobody learns whether an address was asked for before. An
  address is sent one confirmation a day however many ask, and one sender
  (by its /64, below) may ask for 5 a day, so nobody can have many strangers
  emailed, or one stranger often. Opening the link shows what it confirms and
  a button; only the POST that button sends keeps the address, so a mail
  scanner following it confirms nothing. Then it is kept as
  `notify/<PRODUCT>/<the address's SHA-256>.json` - the address, the product,
  its firmware models and when - one per address and product, its address,
  models and removal link in its metadata too. Each
  address has one token (`notify/_who/<hash>.json`, with the products it was
  given for), and one link,
  `https://api.firmwareliberator.com/forget?a=<hash>&t=<token>`, the same in
  every entry and kept as first made. Nothing expires under `notify/`: an
  address stays until its owner removes it, or it is removed by hand. 500
  addresses a day at most. The confirmations are sent by Cloudflare Email
  Service, which sends to any address only on Workers Paid (the account's
  plan since 2026-11-10); its binding
  (`EMAIL`) unbound, `/notify` takes no address (503).
- **Removing an address.** `/forget` is the one path a person opens: a GET
  of the link shows what it will do and a button, and only the POST that
  button sends removes the address, for every product it was given for, and
  its token - so a mail scanner that follows links removes nothing. The
  firewall rule lets a GET through to it and `/confirm` alone (`API_METHODS`).
- **Who a sender is.** Every count of senders - the readouts a key keeps from
  each, the day's 20, the sign-ups, the Worker's rate limit - takes an IPv4
  address whole and an IPv6 one by its first 64 bits
  ([website/api/sender.js](../website/api/sender.js)): one home or phone
  connection is given a whole /64, and could otherwise seem any number of
  senders. One with many IPv4 addresses still can.
- **One sender's readouts.** At most 20 a day are kept from one address
  (`SENDER_DAILY`), counted in the database under an HMAC of the day and the
  address with the Worker's secret `SENDER_KEY`, never the address itself;
  the count is deleted with the day's others by the nightly job. One not kept
  gives its place back. Until `SENDER_KEY` is set, no readout is taken.
  `cloudflare_settings.py apply` makes it when it is missing, and `rotate
  SENDER_KEY` replaces it - which starts the day's counts again, and makes a
  sender's next readout of a key it sent before look like another sender's.
- **Rate.** Before the Worker runs at all, a Cloudflare rate-limiting rule on
  its hostname (`tools/repo/cloudflare_settings.py`) blocks an address sending
  more than 5 requests in 10 seconds, for 10 seconds: a flood never reaches the
  Worker, where every request, even one turned away, counts against the
  account's Workers Paid allowance: 10 million a month included, then billed
  by the million, so a flood past the rule costs money rather than taking the
  API down. Then two readouts a minute
  from one address, held by Cloudflare's rate limiter only. 100 readouts and
  250 MB a day from everyone; past that, the next waits until the next day.
  The day's are counted in the API's database
  ([website/api/counters.js](../website/api/counters.js)), each in a single
  statement, so two readouts at once can never both pass the limit; one not
  stored after all gives its place back. `/wanted` reads the same count.
- **Readouts waiting for review.** One not taken as right - no three
  agreeing, not approved - takes room from 1 GB of the store set aside for
  them (`PENDING_BYTES`, the `readouts:pending` count), given back once it is
  agreed or settled; and it, or one rejected, is deleted with its offered
  files' marks by the nightly job 30 days after it came in
  ([website/api/pending.js](../website/api/pending.js)), so made-up readouts
  can neither fill the store nor hold a key's places for long.
- **Printable text alone.** Every product, model, version, address and the
  like the Worker takes is printable ASCII, and `readouts.py` escapes all it
  prints, so nothing a sender wrote reaches a terminal as a control sequence.
- **One switch.** `INGEST` in the Worker's `wrangler.jsonc`: "on" takes
  requests; anything else - the website workflow can deploy with it off -
  answers every path 503, for the tool to try again later.
- **The database.** Until it has every migration the Worker's code needs
  ([website/api/database.js](../website/api/database.js)), each path answers
  503 and the tool sends again later.
- **Repeats.** The same readout sent again the same day is the one already
  stored.
- **Enough of one device.** A product model listed in `ENOUGH` in the Worker's
  [wrangler.jsonc](../website/api/wrangler.jsonc) is thanked and not stored.
- **Retention.** Each readout is stored privately in the R2 bucket
  `firmware-liberator-readouts` as `readouts/<date>/<hash>.tar`; its
  reference is `<date>-<12 hex>`. Nothing expires: only a readout that adds
  something is kept, so the store grows only with firmware not seen before,
  and an old one may help with a device's later versions. The bucket has no
  lifecycle rule (`cloudflare_settings.py` takes away one added by hand).
  8 GiB of readouts in all at most, counted in the database as the day's are,
  keeps the bucket within R2's free 10 GB with room for `notify/`; past it,
  `/readout` answers 503 and `/wanted` `store-full`, and the tool says to try
  again in a few weeks, until room is made (below).

Nothing of the sender is stored but the contact they chose to give: in the
manifest, and under `notify/`. Nothing serves the bucket.

The bucket, the rate-limiting rule and the API's database are made by `python3 tools/repo/cloudflare_settings.py apply`, by
hand, which also brings the database up to date; the cloudflare-settings
workflow checks all but the database daily. The website workflow deploys the Worker
in a job of its own. [website/README.md](../website/README.md#deploying-it)
says which token does what.

## Sending the confirmations

`/notify`'s confirmations go through Cloudflare Email Service, the Worker's
`EMAIL` binding, from `updates@firmwareliberator.com` (`NOTIFY_FROM` in
[index.js](../website/api/index.js)). It sends to any address only on Workers
Paid (3,000 emails a month included, then $0.35 per 1,000); the sign-up
limits bound what it can cost, at most 500 confirmations and reminders a
day, about 15,000 a month - some $4 past those included. It sends only from a domain onboarded
for sending: `cloudflare_settings.py apply` onboards `firmwareliberator.com`
itself (`SENDING_DOMAIN`), then makes the DNS records Email Service lists
for it - an MX, SPF and DKIM on its bounce subdomain - which the DNS check
expects from then on, as it does Email Routing's. All but its DMARC record:
the zone has its own in `DNS_RECORDS`, and two would void both. Until the
domain is onboarded, a confirmation fails to send and `/notify` answers
502, keeping nothing.

Every email the Worker sends goes through
[mail.js](../website/api/mail.js), which counts what came of it for the day
in the database: `mail-sent:<day>`, or `mail-failed:<code>:<day>` with Email
Service's error code - the admin view shows them, and the nightly alert names
yesterday's failures. An address Email Service has suppressed - it bounced
for good, or its owner marked one of ours as spam - makes `send()` throw
`E_RECIPIENT_SUPPRESSED`: `/notify` answers it as any other address, keeps
nothing and tries it no more that day, and its reminder is not tried again;
those failures are left out of the alert, as some are expected.

## Working with them

```sh
python3 tools/repo/readouts.py
cd website
npx wrangler r2 object get firmware-liberator-readouts/readouts/2026-11-07/0a1b2c3d4e5f60718293a4b5.tar --remote --file readout.tar
```

Both take a token from `CLOUDFLARE_API_TOKEN` that can read the account's R2,
such as the settings check's; putting or deleting one needs one that can edit
it.

- **Making room**, should the store fill: delete readouts no longer needed,
  each with its row in the `readouts` table and its offered files'
  `readouts/firmware/...` marks, then take their bytes off the store's count in the
  database, from `website/` (`npx wrangler d1 execute firmware-liberator-api
  --config api/wrangler.jsonc --remote --command "UPDATE counters SET count =
  count - <n>, bytes = bytes - <bytes> WHERE name = 'readouts:all'"`). The
  usage check warns long before it fills.
- **Duplicates.** The manifest's `flash.sha256` is of the device's own flash
  before it was anonymised, so two readouts of one device share it.
- **Once a device is supported**, add its product model to `ENOUGH`, so no
  more of it are taken; its readouts stay.
- **When a release changes a device's status**, write to everyone who asked
  to be told about it: `python3 tools/repo/readouts.py changes` lists each
  device whose status differs from the latest release's, with each address
  given for it and its removal link (`readouts.py notify <PRODUCT>` lists one
  product's). Run it on your own computer, never in CI, whose logs are
  public. Send each person their own message, never one with every address
  in view, with their own removal link in it (and as its `List-Unsubscribe`
  header, where the sender allows). Say why they are hearing from you: an
  address may have been given by someone other than its owner. An address
  stays on the list after it is written to, for the device's next change,
  until its owner removes it.

Keep what you fetch under `captures/`: like any dump, it never goes into the
repository. [Adding a device](../docs/adding-a-device.md) takes it from there,
as a whole dump.
