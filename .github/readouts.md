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
Sonoff firmware back. That path is proven end to end on the emulated S61.

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
a file the store does not have. One of the whole memory must hold
`flash.bin.xz` unpacking to a flash chip's size, a power of two from 512 KiB
to 32 MiB, which the API reads from the xz file's own index without
unpacking it. The offered files' own format is not checked: a first stage is
mostly of a device the tool does not know, whose files may be in any. The
files are those
[docs/readouts.md](../docs/readouts.md#what-is-in-one) lists, and
`readout.package` and the two `read_and_anonymise` functions write them.

The API relies on this much of the manifest: its `format`, its `model`,
`stock_model` and `stock_version` (together the readout's key), whether the memory
was `read`, and what every genuine readout of that key shares - `firmware`
and `layout` for a whole one, the SHA-256 of each `offered` file for a first
stage - and each `offered` file's `file`, where it is in the tar, or, in
place of the file, its `link` (below). A change to any of these is a change to both sides.

## Where it goes

Readouts go to the project's API, a Worker apart from the website's. The
tool is open source, so it can hold no secret to sign in with. Instead each
request costs its sender some work, the store has hard limits it is never
let past, and only a readout that adds something is kept:

- A readout is kept only while fewer than three of its key are held, each
  from a different sender. Three that agree, from three networks
  (Cloudflare's ASN for each request; a host's or a VPN's counts as none:
  `website/api/networks.js`), are taken as right, and you are
  emailed when they are (the admin view's switch turns that off). Three alike
  from fewer networks wait for one more, with no email: the admin view lists
  them under Nearly agreed, with a button to approve them (or
  `readouts.py approve` one at a time). Any other
  waits for review, in room set aside for it, and is deleted after half a
  year if it is never settled. So made-up readouts can neither fill the store
  nor hold a key's places for long.
- First stages are kept and agree each offer apart: the firmware eWeLink
  offers a device changes with each Sonoff release while the version it runs
  does not, so each new offer has its own three places, which an owner who
  sent the last one may fill too. A whole readout names its offer as well,
  and takes one of those places: an offer is taken as right once three
  readouts naming it, first stages or whole ones, come from three networks,
  and you are emailed (none for a device offered nothing); `readouts.py
  offers` and the admin view's Offers agreed list them. A whole readout
  taken as right on its firmware does not close its offer until then.
  `review` and the admin view name what each was offered.
- One sender sends one first stage of each offer and one whole readout of
  each key. A whole readout after its sender's own first stage of the same
  offer is taken, as every run sends its devices' offers first, but does not
  count towards that offer again: its row names no offer.
- Until three agree, a key has three more places, each for a readout from a
  network none of those held came from: three made up on one rented server's
  network cannot keep out the owners who would outvote them.
- Requests from a network that counts as none, a host's or a VPN's, take
  `RENTED_SHARE` of each day's readouts and offers at most
  (`website/api/index.js`): a flood from rented servers uses up only that,
  and owners on their own networks still have the rest of the day.
- Once a key's whole readouts agree (or you approve them), those places are
  still given, one each `CHECK_DAYS` (`website/api/index.js`), to check them:
  one made up from owners' networks, or approved by mistake, is found by a
  later owner's. One alike is taken as right too (emailed as "Readouts
  confirmed", under the same switch). One not alike waits for review,
  `readouts.py review` marks it DISAGREES, the admin view lists its key
  under Disputed, and you are emailed whatever the switch says; until you
  approve or reject it, readouts of the key from new networks are taken
  without waiting, and no other fingerprint is taken as right on its own.
  It is kept until you do, never deleted with the other pending ones.
- A kept readout is kept for good, in a private bucket that nothing serves:
  an old one may help with a device's later versions.
- Anyone may ask to be emailed as more devices are supported: the tool asks
  as it exits, and the website's sign-up page (`/notify`) asks too, its
  script sending the address to the same `/v1/notify` with the same work.
  The tool's sign-up names the owner's devices and the readouts they sent. The address is kept only once its owner confirms it
  from an email, in the API's database, once an address, and until they
  remove it with the link every such email carries.
- Nothing of a sender is stored but the contact they chose to give. Senders
  are counted under a keyed hash, never by address.

The confirmations and the nightly alerts are sent by Cloudflare Email
Service, from the domain `cloudflare_settings.py` onboards for sending.

## Offers sent as links

A readout can send the files eWeLink offers a device, or only each file's
link and SHA-256, as eWeLink gives them. Which one is your setting:
`OFFERED_SEND` in [wrangler.jsonc](../website/api/wrangler.jsonc), which
`/v1/wanted` and `/v1/offers` pass on to the tool as `send`. While it is
`"files"`, `/v1/offers` keeps nothing and answers each offer `full`, so the
tool sends it again later. A tool told nothing, as every
one released before it, sends files, so the file path always stays.

- **A first stage's links go to `/v1/offers`**, every device's in an account
  in one request, sent unasked as reports are. Each offer is held as a first
  stage of its device is, under the same key (`index.js` `keyOf`; a cloud
  other than eWeLink would add its own part), so links, first stages with
  files and whole readouts agree on an offer together, each sender once. Its
  row's object is a small JSON manifest, its links kept in `offer_links`.
  `OFFERS_PER_REQUEST`, `SENDER_OFFERS_DAILY` and `OFFERS_DAILY` in
  `index.js` hold them; one held already counts against none.
- **A whole readout's links are in its manifest.** Each `offered` entry is
  then `{"file": null, "version", "sha256", "link"}`, with no file under
  `offered/` for it, and the manifest says `"cloud": "ewelink"`. The API
  checks each link as an offer's (its path's shape, its SHA-256), turns the
  readout away if one is not, and keeps them in `offer_links`. The readout's
  offer is still its files' SHA-256s, and its fingerprint its firmware.
- **The fetch, on first sight.** Each file a kept offer or readout links,
  not held, is fetched by that link after the answer, from eWeLink's file
  server by name ([offered.js](../website/api/offered.js) `CLOUDS`), and kept
  only if its SHA-256 is the one given. This needs no agreement: a file
  fetched from eWeLink proves itself, and eWeLink's files are a finite set,
  so however many offers are made up the store holds no more than those. A
  link that does not give its file is noted (`offer_fetch_failures`) and not
  tried again for that SHA-256 on first sight, so the same made-up link sent
  again costs nothing. A file kept is its mark under `readouts/firmware/`,
  listed under the readout whose link fetched it, with the firmware names its
  Sonoff wrapper gives (custom metadata `names`, joined by `+`; none for an
  ESP8266 file), for you to review; nothing is refused on them.
- **Agreement still says whose offer it is.** Once an offer is taken as
  right, each of its files still not held is tried by the links given for
  it, `OFFER_FETCH_PATHS` at most, those from the most networks first. A link that gave another file is
  never tried again for it; one that gave nothing is, once a day.
  `OFFER_FETCHES_DAILY` files are fetched a day at most, across everyone,
  first sight's and agreement's alike. Of those, one sender's offers and
  readouts get `OFFER_FIRST_SIGHT_PER_SENDER` first-sight fetches a day, so
  one sender's made-up links cannot use up the day before owners' files are
  tried; a file past it is fetched once its offer agrees, which no sender's
  count holds.
- **A file not given** is tried again each night. One still missing
  `OFFER_FETCH_ALERT_DAYS` after its offer agreed is in the nightly email.
- **A link of a shape not seen before** is sent whole as `url`, in a
  `/v1/offers` file in place of `path`, or in a whole readout's `offered`
  entry beside its file, which the readout still carries. Its rules are
  `linkParts` in [offered.js](../website/api/offered.js): http or https, a
  host that is a name of eWeLink's own (`CLOUDS` `hosts`) or a public IPv4
  address, a port of `URL_PORTS` or none, a path of letters, digits and
  `. _ - /` alone, no query or fragment. One that breaks a rule turns the
  request's offer, or the readout, away. It is kept in `offer_links` as its
  parts, `review` `pending`, and never fetched, on first sight or at night,
  until you approve it.
- **A peek at it, once.** When a link to review is first taken, its first
  `PEEK_BYTES` are asked for once (a Range, no redirect followed,
  `PEEK_TIMEOUT_MS` a server), from where approving it would fetch it, each
  request counted as a fetch, the sender's too. What came back is kept in
  `link_peeks`: the status, the file's size if `Content-Range` gave it, the
  server that answered, what the bytes start like (`PEEK_KINDS`: Beken's
  FOTA container, Sonoff's wrapper with its firmware names, an ESP8266
  image, a zip, or unknown) and the bytes as hex. A redirect is recorded,
  with where it pointed, and not followed.
- **Approving or rejecting it** is the admin view's Links to review. Each
  link pending is there once, however many sent it, with its device, its
  SHA-256, how many networks sent it, when it was first seen, its peek, and
  the link as sent with a copy button. Look at the link and its first
  bytes; approve it if it is eWeLink's file server giving a firmware file.
  Approving fetches the file at once, within `OFFER_FETCHES_DAILY`, with
  `CLOUDS`' `query` added, as eWeLink's servers answer a request without
  one with an empty 400: from a host that is eWeLink's name, by the link as
  sent; from an address, never that address, but its path from each of
  eWeLink's servers by name (`CLOUDS` `servers`). The page shows what
  approving would ask for as "Approving tries". It is kept only
  if it is the SHA-256 given. Approved and not yet held, it stays listed,
  saying whether a server was tried and gave nothing, with Try again; once
  approved, it is also tried as an agreed offer's links are. Rejecting it
  means it is never fetched.
- **The admin view's Offers sent as links** counts the readouts that sent
  links, by key beside each key's offers agreed, the files fetched from
  links and their bytes, the files still missing for an agreed offer, and
  the day's fetches against `OFFER_FETCHES_DAILY`. Its Every link lists each
  file's link, as the API fetches it (a link to review not approved, as
  sent), each with a copy button.

Set `OFFERED_SEND` to `"links"` once a released tool reads `send`, and watch
the first files arrive and offers agree. Set it back to `"files"` when
the nightly email says files are not being fetched and eWeLink is the cause:
its file server checking the device's id, say, or links no longer lasting.
Every tool in the field sends files again from its next run, with no
release.

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
3. Nothing more for an address that bounced for good or complained:
   `readouts.py notify` and `changes` take every such address on the
   suppression list off the list before they show it. Leave it suppressed,
   so it is never written to again, unless its owner asks to be told once
   more.

An email you send yourself bounces to your own mailbox instead: take that
address off with `readouts.py forget <address>`.

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

`readouts.py` reads `CLOUDFLARE_READOUTS_REVIEW_TOKEN` from the environment or
`tools/repo/.env.infra`: the readouts token, allowed the API's database and to read the
buckets and the suppression list, which `tools/repo/cloudflare_tokens.py readouts-review` makes.
wrangler reads its own name, `CLOUDFLARE_API_TOKEN`: set it to the same
token. Putting or deleting an object needs one that can edit the bucket.
`readouts.py --help` lists its other commands.

`readouts.py` lists the readouts (`readout`, a tar) and the offers sent as
links (`offer`, a JSON manifest), each line saying which. `readouts.py
firmware` lists the offered files under `readouts/firmware/`: each one
`fetched` from its link, its bytes there, or a `mark` of one a readout holds,
with that readout's object.

- **Reviewing.** `readouts.py review` lists the readouts waiting, by key,
  with an odd one's fingerprint marked; `approve ID` or `reject ID` settles
  one. A rejected one frees its place for another sender's.
- **Duplicates.** The manifest's `flash.sha256` is of the device's own flash
  before it was anonymised, so two readouts of one device share it.
- **Once a device is supported**, add its product model to `ENOUGH` in the
  API's `wrangler.jsonc`, so no more of it are taken. Its readouts stay.
- **Making room**, should the store fill (the nightly alert and the usage
  check warn long before): delete readouts no longer needed, each with its
  row in the `readouts` table, its offered files' `readouts/firmware/...`
  marks and its `offer_links` rows. Then take their bytes off the store's
  count, from `website/`:

  ```sh
  npx wrangler d1 execute firmware-liberator-api --config api/wrangler.jsonc --remote \
      --command "UPDATE counters SET count = count - <n>, bytes = bytes - <bytes> WHERE name = 'readouts:all'"
  ```

- **When a release changes a device's status**, write to the people on the
  list.
  1. Run `python3 tools/repo/readouts.py changes` on your own computer, never
     in CI, whose logs are public. It lists each device whose status differs
     from the latest release's, with the owners of it on the list and each
     one's removal link. `readouts.py notify` lists everyone, for news of a
     newly supported device; `readouts.py notify <PRODUCT>` one product's
     owners.
  2. Send each person their own message, never one with every address in
     view, with their own removal link in it and in two headers, so their
     mail program offers one-click unsubscribe (RFC 8058, which Gmail and
     Yahoo expect of a list): `List-Unsubscribe: <the link>` and
     `List-Unsubscribe-Post: List-Unsubscribe=One-Click`. `/forget` takes
     that program's POST to the link.
  3. Say why they are hearing from you: an address may have been given by
     someone other than its owner.

  An address stays on the list after it is written to, until its owner
  removes it. Its devices are every one its owner's eWeLink account held
  when the tool ran, each with when it was first and last seen (a device
  is never taken off: one gone from the account was most likely
  converted), and its readouts the ones they sent: the owners to ask to
  try a build.

Keep what you fetch under `captures/`: like any dump, it never goes into the
repository. [Adding a device](../docs/adding-a-device.md) takes it from
there, as a whole dump.
