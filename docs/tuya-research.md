# Tuya / Smart Life support: feasibility research

Whether the tool's approach — convince a device to fetch and apply a firmware
of your choosing over the air, with no soldering — can work for Tuya / Smart
Life devices as it does for Sonoff's eWeLink ones, and how that was found out.

**Status: investigated 2026-11-24.** The headline question is answered; a few
leads for a narrower path are left open and marked below. Nothing here has been
run on a real Tuya device, and the tool has no Tuya code. Findings are marked by
how they are known: **[P]** primary source (vendor or tool code, official doc);
**[O]** the repository's own analysis of a public dump; **[H]** community report, not
independently confirmed.

Private per-device material stays out of this repository, as everywhere else.
Firmware dumps studied here are public (see [Dumps studied](#dumps-studied)) and
live in `captures/tuya/`, which is private and never committed. Each dump
contains its donor's own `uuid`/`authKey`, which is one reason they are not
committed.


## The short answer

**An eWeLink-style "any URL + hash" conversion is _not_ possible for current
(patched) Tuya devices without opening them.** It _is_ possible, over the air,
for devices whose firmware predates Tuya's February 2022 SDK fix — and the
existing tool **tuya-cloudcutter** already does exactly that. So:

- **Old / unpatched Tuya devices (pre-Feb-2022 firmware):** liberatable over the
  air, no soldering, to OpenBeken / ESPHome(LibreTiny) / Tasmota. The path is
  cloudcutter's AP-mode exploit + fake-cloud OTA. A shrinking but still real
  population. For these, the sensible move is to **invoke/point at cloudcutter**,
  not rebuild it.
- **Patched Tuya devices (most sold since ~2023):** **no over-the-air-only
  path exists.** Pushing a firmware requires the device's per-unit `authKey` /
  `secKey`, which are not exposed over the air. Getting them needs physical
  access (UART log or flash dump) — and on the **newest TuyaOS (3.8+) that is
  itself uncertain**: in the dumps here the keys were no longer in the readable
  storage record, the boot-time UART key print is reduced, and the online
  `pskKey` lookup may be defunct (see [the key problem](#the-key-problem-why-patched--no-ota)).
  Once a device is open on serial you would anyway just **UART-flash
  OpenBeken/LibreTiny directly**, so the OTA mechanism adds nothing. For patched
  devices the tool's OTA model does not apply, and for the very newest ones an
  OTA conversion may not be possible by any public method at all.

The reason is structural, and it is the one thing to take away:

> Tuya's OTA integrity is an **HMAC-SHA-256 keyed with the device's own
> `secKey`** — a per-unit secret. There is **no vendor (Tuya) signature and no
> secure boot** on the Beken Wi-Fi image, so a stand-in cloud *can* push any
> firmware — **but only if it already holds that device's keys**, and the key it
> ultimately needs (`authKey`) is never exposed over the air on a patched device.

eWeLink, by contrast, checks a plain unkeyed SHA-256, so knowing nothing but the
device's network address is enough. That single design difference — keyed vs
unkeyed hash — is what makes eWeLink liberatable by a lightweight tool and Tuya
not.


## How Tuya's OTA works

Reconstructed from tuya-cloudcutter's fake-cloud code and Tuya's own open SDK
[P], and confirmed against strings/disassembly in a TuyaOS 3.11 app image [O].

1. **Find the cloud.** On boot the device resolves a hard-coded address server,
   `h3.iot-dns.com` (`h3-<region>.iot-dns.com`), and POSTs to `…/v1/url_config`
   (older: `/v2/url_config`, `/device/url_config`, `a.tuyacn.com`) [P, Tuya SDK
   `uni_network.h`; O, in every dump checked]. The reply hands back the working
   endpoints (`httpUrl` → `…/d.json`, `mqttUrl`, and the PSK-TLS variants) and a
   `caArr` CA list.
2. **Activate.** The device calls `tuya.device.active` on `/d.json`, encrypted
   with its factory **`authKey`**. The response delivers the per-session
   **`secKey`** and **`localKey`** and the DP `schema`. Thereafter `/d.json` is
   encrypted with `secKey` [P, cloudcutter `pull_schema.py`].
3. **Trigger.** The cloud publishes an MQTT **protocol-15** message to
   `smart/device/in/<devId>` (AES-encrypted with `localKey`): "an update waits."
4. **Ask for the image.** The device calls **`tuya.device.upgrade.get`** (or
   `…upgrade.silent.get`) and the cloud replies:

   ```json
   { "url": "http://<host>/files/fw.bin", "hmac": "<UPPER-HEX>",
     "version": "9.0.0", "size": "<bytes>", "type": 0 }
   ```

   with **`hmac = HMAC-SHA256(secKey, UPPERCASE-HEX(SHA256(file)))`**
   [P, cloudcutter `__main__.py` `upgrade_endpoint_hook`; P, Tuya open SDK
   `tuya_ota.c` computes the same; O, 3.11 app strings
   `begin to download file … hmacKey:%s`, `file hmac check success/fail`,
   `tuya_svc_upgrade.c`].
5. **Download + verify + apply.** The device HTTP-GETs the URL (ranged), streams
   to flash, and checks the file HMAC with `secKey` before swapping. The image
   is a Tuya **"UG" file** (magic `55 AA 55 AA`) wrapping the chip's native
   container (Beken RBL).

### No signature, no secure boot (the gate that *isn't* there)

The 3.11 **bootloader verifies only a hash and a CRC-32** of the image
(RT-Thread OTA framework: `rt_ota_part_fw_verify`, `part_fw_hash_verify`,
`Verify firmware CRC32…`), with **no RSA/ECDSA public-key check** [O]. The
`pubkey`/`signature` strings in the app belong to the BLE/Wi-Fi *pairing*
handshake, not OTA. Tuya's open SDK `tuya_ota.c` likewise gates only on the
secKey-HMAC [P]. Beken flash-encryption of the stored image is **not** a barrier
— the chip keys are public (LibreTiny) and the repository's Beken tooling
already handles it [P].

The same holds on **Realtek RTL8720CF** (the ET31 smart plug, SDK 2.3.3): no
secure-boot/asymmetric-boot code at all (no `secure boot`/`RSIP`/`sboot`/RSA/ECDSA
verify strings), boot integrity is the **AmebaZ2 image-format magic** (`99 99 96
96` manifest, `Invalid FW Image Signature!`) plus **CRC-32 and an MD5** on the OTA
file; the `update_ota_signature` / `… signature:` strings are the Tuya OTA-layer
**secKey-HMAC** append, not public-key signing [O]. So on the builds examined —
Beken 3.11 and RTL8720CF 2.3.3 — an arbitrary **unsigned** image boots once
written; the only barrier is the OTA secKey-HMAC on the push, never a boot-time
signature.

Not yet instruction-audited: the **BK7238 T-series bootloader** specifically (the
chip this repository's Beken route targets — the secure-boot verdict above rests on the BK7231N 3.11 RT-Thread verify
path and the absence of asymmetric-verify code in the BK7238/RTL8720CF apps, not
on a disassembly of the BK7238 boot ROM/loader itself), and the very newest
builds. T3/T5 modules ship an SDK (Armino) that *contains* TF-M / PSA secure-boot
components [P], so secure boot is *possible* there; no evidence it is enabled on
retail devices [H].

### The LAN-OTA path does not rescue it

The 3.11 app does carry a **LAN upgrade** routine (strings `Rev lan ota start`,
`lan ota json content error`, `lan upgrade timeout`; a JSON parser for
`{otaChannel, otaVersion, fileHmac, isDiffOta, otaFileLen}`) [O]. This looked, at
first, like a cloudless route needing only `localKey`. It is not, for two
independent reasons:

- **Same apply routine, same key — confirmed at the byte level.** The LAN JSON
  parser and the cloud path converge on the *same* `tuya_svc_upgrade.c`
  download-and-verify function [O]. That function computes its HMAC key as
  `ctx + 0x470`; the network-config handler stores **`secKey` at `ctx + 0x470`**
  and **`localKey` at `ctx + 0x481`** (16-byte fields, `strncpy` len `0x11`).
  So the OTA file-HMAC is keyed by **`secKey`**, *not* `localKey` — proven, not
  inferred [O]. Since `localKey` (the one the owner's cloud account exposes) is
  the wrong key, and `secKey` requires `authKey` to obtain, LAN OTA gives no
  shortcut. The `skip hmac check` log string on one branch is a **fast path** (a
  string compare that, on a match, skips a redundant binary compare) — **not** a
  bypass.
- **No documented LAN trigger.** Tuya's own LAN service header (`tuya_svc_lan.h`,
  frames `0x01–0x12, 0x14, 0x40, 0x42`) and `tuya_lan.c` expose commands,
  queries, the key handshake, heartbeat and P2P — **no firmware-push and no
  cloud-URL command** [P]. Whether the retail `lan_ext_cmd_parse` extension
  reaches the OTA routine is unconfirmed, but moot given the secKey requirement.


## The key problem (why patched = no OTA)

Everything hinges on holding the device's keys, which fall in two tiers:

- **`localKey`** (+ `devId`, `uuid`) — the *control* key. The owner's **own
  cloud account exposes this**: tinytuya's wizard against the free Tuya IoT
  developer platform returns `device_id`, `local_key`, `uuid`, MAC and SN — and
  **not** `authKey`/`secKey`/`pskKey` [P, tinytuya `Cloud.py`]. Enough to control
  a device over LAN; **not** enough to push firmware.
- **`authKey`** → (`secKey`, `pskKey`) — the *identity* keys, factory-burned.
  cloudcutter's `pull_schema` states it **"requires a valid device uuid, authkey,
  product key _from a firmware dump_"**, then talks to Tuya's device-facing API
  *as the device* to fetch `secKey`/`pskKey` [P]. `authKey` is an **input you
  must already have** — from the pre-2022 exploit, a flash dump, or UART logging
  on devices that print it. No account surface hands it out [P].

So the acquisition routes, and what each costs:

| Route | Needs | Soldering? | Works on patched? |
|---|---|---|---|
| **A. cloudcutter exploit** (AP-mode overflow → keys → fake-cloud OTA) | nothing but RF proximity | **No** | **No** (pre-Feb-2022 only) |
| **B. Flash dump / UART** → keys → fake-cloud (or known-secrets) OTA | the keys | usually | **Uncertain on newest TuyaOS** (see below) |
| **C. User cloud account** (`localKey` only) | account login | No | push: **No** |

Route A is the only no-solder route and it is closed on patched devices. Route C
can control but cannot flash. **There is no fourth route**: the OpenAPI OTA
endpoints are deprecated and read-only, firmware is uploaded only by the
product's owner in Tuya's console, and a user cannot push to a product they do
not own [P].

**Route B is not the sure thing it looks** — a key read on current firmware is
uncertain in three ways [O, across the donor dumps here; the UART-print and API
items are **unverified**, not disproven]:

- **UART no longer clearly leaks the keys on TuyaOS 3.x.** The log port is UART2
  = **pin P0 (TX)** / P1 (RX) on Beken [P, LibreTiny]. The full
  `upd product_id … <key> <uuid> <authKey>` line that cloudcutter's
  known-secrets doc relies on appears **only in pre-3.x builds** of the dumps
  here; TuyaOS 3.x prints a reduced one-field `upd product_id` line. The
  `uuid=%s auth_key=%s` / `pskKey=%s` format strings still exist in the 3.x
  images but are table-driven, and whether they are printed at boot was **not
  confirmed**. So UART may or may not yield `authKey` on a given 3.x device —
  it takes one boot-log capture on the actual unit to find out.
- **A flash dump may not contain the keys on TuyaOS 3.8+.** The storage
  partition is at `0x1EE000`; the identity record `gw_bi` (uuid 16, `psk_key`
  37, `auth_key` 32, `fac_pin` 16) is fully populated on the **pre-3.x / early**
  donors — but on the **TuyaOS 3.8 and 3.11 donors here it was empty or absent**
  (they carry only `kv.ccode.active`/`.mf` instead). On the patched BK7238
  **TuyaOS 3.11.11** builds the keys live in the **flash KV store, but as an
  encrypted/license blob, not a plaintext record**: the whole logical image was
  scanned and every `auth_key`/`gw_bi`/`gw_di`/`gw_ai` hit is in the code/format
  region, while the KV data tail is sparse (0xFF, entropy ~1.5–1.9) with **no
  plaintext `gw_bi` key record** [O]. The firmware reaches the keys through
  `tuya_iot_license_read()` order — **KV → module OTP flash → build-time macro**
  [P] — resolving on these modules to the KV blob; the read routine (at `0x0d5180`
  in TH08, via helper `0x0d4c9c`) pulls uuid/`auth_key`/`psk_key` with lengths
  `0x1a`/`0x21`/`0x41` into RAM (`gen_auth_key`/`gen_psk_key`, guard
  `please write uuid and auth_key first`) [O]. So a flash dump **does** still hold
  `authKey` on 3.11 — but only after a **KV-blob decrypt** (what cloudcutter's
  `process_storage.py` does for the old `gw_bi`; the newer encrypted layout is why
  a raw dump shows nothing). cloudcutter's `process_storage.py` reads
  `uuid`/`auth_key`/`fac_pin` from `gw_bi` and never `psk_key`, so it assumes the
  old layout. (The other records are `gw_di` — product/firmware keys `pk`/`firmk`
  — and `gw_ai`, whose `mq_psk`/`hs_psk` are cloud-*channel* PSKs, **not** the
  device `pskKey`.)
- **The API that derives `pskKey` from `uuid`+`authKey` may be dead.**
  cloudcutter's `pull_schema` posts to `a.tuya<region>.com/d.json`
  (`tuya.device.active`, `tuya.device.uuid.pskkey.get`), but its own README caps
  the Smart Life flow at app ≤ 4.8.2, and community reports describe the login
  and IoT-platform key lookup now blocked. **Unverified today** (no live calls
  were made — that needs a real `uuid`/`authKey`).

The upshot: for a genuinely current TuyaOS 3.8+ device, it is **not established
that any public route — wireless or wired — recovers the keys**, which lines up
with cloudcutter's own "Known Patched Firmware" list growing and the community
view that the newest devices are hard or impossible. For such a device the
honest answer may be "no working OTA method today", and the reliable fallback is
**direct UART flashing of OpenBeken/LibreTiny** (bypassing Tuya's keys and OTA
entirely) — which needs the same P0/P1-plus-boot-mode access as a dump, so once
you are on serial, OTA buys nothing.

### No remote key-recovery surface, and interception is useless (audited)

Two attacker paths that would sidestep the physical read were checked directly
against the patched BK7238 TuyaOS 3.11.11 app and found closed [O, confirmed
across three dumps]:

- **No network/LAN/MQTT/BLE command discloses the keys.** Every network-facing
  parser was audited. The only unauthenticated on-LAN surface, the LAN
  `query_dev` reply, returns `{"ip","gwId","uuid","productKey",…}` — `uuid` and
  `productKey` only, both **non-secret** (productKey is printed in the dump file
  name); it is how cloudcutter learns the `uuid`, not the blocker. MQTT handlers
  (`smart/device/in/…`, `localkey.upd`) only **receive** cloud-pushed values; the
  atop `/d.json` APIs (`tuya.device.active`, `…upgrade.get`, `…uuid.pskkey.get`)
  are all **device→cloud** requests whose `secKey`/`localKey` strings are JSON
  keys used to *parse* the authKey-encrypted response, not to emit anything; BLE
  netcfg carries only ECDHE pairing material and SSID/token. **No info-leak,
  out-of-bounds read, or format-string-on-a-key was found in any network path**;
  the `uuid=%s auth_key=%s`-style `%s`-on-key format strings belong to the **UART
  debug log**, not a socket. The single handler that *does* emit
  `auzkey`/`uuid`/`pskKey` is the fuse-gated factory production-test interface
  above, reachable only over the local factory Wi-Fi AP — never cloud or LAN.
- **Sniffing or MITMing cloud↔device traffic yields nothing.** `secKey`/`localKey`
  **do not exist before activation**; they are born only inside the
  `tuya.device.active` response, which is **AES-128-CBC encrypted and signed under
  the device's `authKey`** [O, `MBEDTLS_CIPHER_AES_128_CBC` in atop; P, SDK]. The
  PSK-TLS channel is itself **authKey-chained** — the `pskKey` is derived from
  `authKey` (`gen_psk_key`) or fetched over the already-authKey-authenticated atop
  session — so a peer without `authKey` cannot even complete the handshake. The
  SL0 `/v1/url_config` exchange returns only endpoints + `caArr`, **no key
  material**. A passive sniffer sees only authKey-ciphertext; an active MITM can
  neither forge a valid `active` response (authKey signature) nor decrypt the real
  one. There is **no downgrade and no known/guessable key** that turns
  interception into a key leak. (These are **security-level-0** builds — the
  easier case for the register-center redirect below — yet interception still
  fails, because the barrier is the per-unit `authKey`, not the transport.)

### The register-center override: a cleaner redirect, same blocker

A lead worth investigating turned out **real in retail firmware** but does not
change the verdict — it only makes the *redirect* step cleaner.

Tuya's **pairing** message carries an optional register-center override: the
AP/BLE netcfg JSON may include a **`reg` object** (`pub`, `url0`, at security
level ≥1 also `url<SL>` + `ca`) which is saved via
`tuya_register_center_save(RCS_APP, …)` to the KV key `rcs.active`, preferred
over the factory `rcs.mf` [P, open SDK `ap_netcfg.c` `ap_cfg_cmd_patse` reads
`reg` and saves it; `ble_netcfg.c` does the same; `tuya_register_center.c`]. The
save is gated only by the presence of ssid/token/pwd — **no authentication** —
and the address server it names then supplies the atop/MQTT hosts and the CA
(`caArr`) the device trusts for activation [P, `iotdns.c`, `atop_base.c`]. So an
**owned device can be pointed at a cloud of your own using only its documented local
pairing protocol — no exploit, and no DNS spoofing.**

Crucially, this is confirmed **present in the retail 3.11 dump**, not just the
open SDK: strings `Parse reg`, `app reg:%s`, `rcs.active`, `rcs.mf`,
`def rcs url0:%s`, `rcs.url0:%s`, `tuya public url`, `third cloud url`,
`https://%s/v2/url_config`, `caArr` all appear [O]. (The SL≥1 keys
`url1`/`url2`/`url3` do **not** appear — consistent with a security-level-0 build,
which carries no CA in the register center and so is the easier case; the device
reports its actual `sl` over the air via `query_dev`, no opening needed.)

Specifics of the override and the pairing it rides on [P, open SDK; O where
noted]:

- **`reg` fields.** `pub` and `url0` are required; at security level ≥1 also
  `url<SL>` and `ca` (a PEM, converted to DER); `code` is optional. **Region is
  not overridable** (`DEF_RCS_URL0_REGION` only builds defaults). Default hosts:
  `url0` = `h3.iot-dns.com`, SL1 = `h6.iot-dns.com`, SL2/3 = `h4.iot-dns.com`.
- **Trust by security level.** SL0 reaches the address server over TLS-PSK with
  **no CA**, so there is nothing to pin and the `reg` needs no `ca`; SL≥1 pins a
  firmware-held ECDSA CA, and the `reg` must then carry a matching `ca`. After
  `url_config`, the atop/MQTT trust descends from that register-center CA (there
  is no separate Tuya-root pinning on the atop/MQTT path, though a
  `tuya public url` branch in `tuya_cert_manager.c` loads built-in roots and was
  not traced). The device's separate `/v1/root_ca` update **is** signature-checked
  (retail `ca sign pass` / `dev sign:%s, cloud sign:%s`); the signing key is
  unknown [O].
- **AP pairing transport.** The AP-mode channel is TLS-PSK: a fixed
  `123456`/`psk_identity` when no pincode is set, otherwise the PSK is
  `pbkdf2(pincode, uuid)`. The pairing broadcast advertises `sl`, `apConfigType`,
  `encrypt` and whether a `token` is required. The activation `token` itself
  reaches the device over this channel or, without one supplied, is awaited as a
  Smart Life multicast on **UDP 6669**.

**But the override does not supply `authKey`, and that is the whole blocker.**
Activation (`thing.device.opensdk.active` on `/d.json`) is **AES-GCM encrypted
and MD5-signed with the device's factory `authKey`**, and the device accepts an
activation response **only if it decrypts under that key** [P, `atop_base.c`,
`atop_service.c`; the device checks the token is merely non-empty — the *cloud*
validates it, not the device]. Neither AP nor BLE pairing ever discloses
`authKey`; it is factory-provisioned (`tuya_authorize.c`, a license blob in
KV/OTP). So redirecting the device to a cloud of your own gives a device that will *talk*
to it but refuse to activate until its `authKey` is known — back to the
same key problem, obtainable only by UART/dump/(pre-2022)exploit.

**What this does change:** *given* `authKey` (e.g. read once over UART), the rest
of a local re-onboard is plausible from documented protocol alone — factory-reset
to unactivated, pair with a `reg` override aimed at that cloud, serve
`url_config` + an `authKey`-encrypted `active` response (secKey/localKey/schema)
+ the OTA offer. That is a **cleaner, DNS-spoof-free variant of cloudcutter's
known-secrets flow**, not a new capability: it still needs the one-time key read.
(The factory reset is required, not optional: an activated device **ignores**
re-pairing — retail strings `NOT ble actived, ignore add ssid/passwd cmd` and
`ignore set wifi cmd` — so it must be returned to the unactivated state first
[O].)

### Where `authKey` comes from: factory authorization

`authKey` (with `uuid` and `pskKey`) is written into the chip at manufacture, in
an **authorization** step: a factory tool stores a license blob — `auzkey` (the
`authKey`), `uuid`, `pskKey`, `ap_ssid`, `fac_pin`, `prod_test` — into KV or OTP
[P, open SDK `tuya_authorize.c`]. The device holds these from then on; the
pairing and LAN protocols never emit them.

The retail firmware still contains the factory **production-test interface**
(`ty_gfw_prod_test.c` / `prod_test_mcu.c`, Tuya "wifi production test") the line
uses to read that blob: a request `{"uuid":"%s","fac_pin":"%s","app_name":"%s",
…}` and a reply `{"ret":true,"auzkey":"%s","uuid":"%s","ap_ssid":…,"pskKey":"%s",
"check_pin":true,"fac_pin":…,"prod_test":…}`. Its reply carries exactly the
`authKey`/`uuid`/`pskKey` triplet that is otherwise the key blocker. Disassembly
of a patched BK7238 TuyaOS 3.0.22 app (TH08, cross-checked against a second
BK7238 3.0.22 dump) settles how it is reached and gated [O]:

- **`fac_pin` is not an access control.** The reply builder emits `auzkey`,
  `uuid`, `pskKey` and `ap_ssid` **unconditionally**; `check_pin`/`fac_pin` are
  status fields echoed back (`"check_pin":true,"fac_pin":null,"` vs `…"fac_pin":
  "%s","`). There is **no `strcmp`/`memcmp` of the request `fac_pin` against any
  stored secret** anywhere in the handler — a correct PIN is neither required to
  obtain the keys nor able to block them. The disclosure is deliberate; the
  secret it leaks is real [O, reply builder and the absence of any pin compare].
- **The real gate is a one-way factory fuse, `mf_test_close` (a KV flag).**
  `mf_init` calls a check that reads `kv_get("mf_test_close")` and, if it equals
  `"true"`, logs `have actived over 15 min, not enter mf_init` and returns
  immediately — production test is then **permanently refused**. The firmware
  sets the flag `true` once the device has been **activated for >15 minutes**
  [O, proven writer `kv_set("mf_test_close", …)`; the activation+15-min trigger
  read from the log string, not a traced timer]. Entry is further guarded by
  early network-state flags (a connecting/unprovisioned state).
- **Entry transport is local Wi-Fi, not the cloud/LAN.** In production test the
  **module scans for and joins** an AP named `tuya_mdev_test<N>` (seen:
  `tuya_mdev_test4`) with the **hardcoded WPA2 password `12345678`**, then
  exchanges the cJSON request/reply above. Per Tuya's SDK docs the module enters
  this mode when the **external MCU sends the production-test command over
  TuyaMCU UART** (~5 s after power-on); the module then performs the Wi-Fi
  exchange [P, Tuya wifi-production-test docs; O, `tuya_mdev_test`/`12345678`
  constants and the STA-join in `mf_init`]. No cloud, MQTT or LAN-service
  command reaches this handler.

**Risk, resolved.** On a **provisioned/activated** retail unit the `mf_test_close`
fuse is set, so `mf_init` returns early: the device never joins the test AP and
the keys never leave — **this interface is not a remote or on-LAN key-recovery
route**, which is why `authKey` stays the sole blocker. A **narrow local-RF
exposure** remains only for an **unprovisioned / factory-fresh** unit whose fuse
is still open: an attacker in RF range who stands up SSID `tuya_mdev_test4` /
`12345678` and speaks the cJSON protocol would receive `auzkey`/`uuid`/`pskKey`
with no PIN challenge. Whether that case is reachable without physical access
turns on one unresolved question — **does an unprovisioned module auto-enter
Wi-Fi production test on boot, or only on the TuyaMCU-UART command?** The SDK
path needs the UART trigger (which requires opening the device); if a retail
unit instead auto-enters while unactivated, factory-fresh or just-reset units
would be locally harvestable over the air. This needs a live unit to settle (and
to confirm `mf_test_close` ships closed, and that factory reset does **not**
reopen it). The same interface strings appear in the RTL8720CF (ET31) app; see
[chips](#chips-and-the-fit-with-the-repositorys-tooling).

This is also what the pre-2022 exploit (route A) attacked: cloudcutter's payload
is an **authorization write** — `{"auzkey": <fresh key>, "uuid": <fresh>,
"pskKey": "", "prod_test": false, "ap_ssid": …}` plus the overflow `token` — so
the exploit drives this same authorization path *unauthenticated* and
**re-authorizes the device with its own freshly generated keys** rather than
recovering the stock ones [P, cloudcutter `exploit.py`]. That is why it needs no
prior secrets, and why hardening this path in Feb 2022 closed it.


## Chips, and the fit with the repository's tooling

| Tuya module | Chip | Repository tooling | Notes |
|---|---|---|---|
| WB3S/WB2S/WB3L (older) | BK7231T | BekenEmulator [P] | cloudcutter/Lightleak most stable here |
| CB3S/CB2S/CBU (common) | BK7231N | BekenEmulator [P] | the bulk of OpenBeken targets |
| **T1-series (newer)** | **BK7238** | **the Beken emulator** | closest drop-in to current route |
| T2 | BK7231N | BekenEmulator | [H] |
| T3 | BK7236 | — | Armino SDK; secure boot *possible* |
| T5 | BK7258 | — | Armino SDK |
| WR / WBR | RTL8710BN / RTL8720CF | — | Realtek; cloudcutter covers them; the repository does not. ET31 (RTL8720CF) analysed: patched by build date, no secure boot, encrypted-KV keys — see below |

The overlap is strong where it exists: same emulator, same RBL/flash tooling
(`unstripe.py`, `layouts.py`, `bk7231tools`), same OpenBeken/ESPHome targets, and
OpenBeken/LibreTiny already ship **per-device pin maps** for thousands of Tuya
devices — the head start the eWeLink side never had. Pins are still never
guessed, the same rule as for every other device.

### ET31 (Realtek RTL8720CF) — a second chip family, same verdict

The Eightree ET31 UK smart plug (module `oem_rtl8720cf_dltj`, `module:WB3S`, fw 1.3.7)
was analysed to test whether an older SDK line is softer. It is not [O, strings +
build date; P, cloudcutter/rb9.nl write-up]:

- **Patched — cloudcutter path closed.** The 2 MB flash holds two OTA slots: the
  **active** one is `TUYA IOT SDK V:2.3.3`, **built 2023-01-05**, ~11 months past
  Tuya's Feb-2022 cutoff (cloudcutter's "Known Patched Firmware" lists SDK 2.3.3
  `BUILD AT 2022_03_02` as patched); the dormant upper slot is a pre-patch 2.3.2
  from 2021 but is not remotely selectable. The AP-netcfg v3 parser cloudcutter
  attacks (UDP 6669, `{"ssid","passwd","token"}`, the unclamped `token` →
  `finish_cb` overwrite in `ap_netcfg.c`) is present but compiled inside the
  patched window, so the bounds check is there by build date. _Caveat:_ patched
  status rests on the build date (cloudcutter's own criterion), not an
  instruction-level check of the token clamp — the one thing a deeper disasm or a
  live cloudcutter profile attempt would add.
- **No secure boot** (as above): Ameba magic + CRC-32 + MD5 only.
- **Same key posture.** `authKey`/`uuid`/`pskKey` are an **AES-128-ECB-encrypted
  KV blob** (`aes128_ecb_decode_raw err when read flash(key data)`,
  `key data is not matched magic/crc32`), not plaintext — matching Beken 3.11.
  _Open:_ whether the KV AES key is device-unique or a fixed SDK constant (if
  fixed and known, a flash dump yields keys directly; not determinable from
  strings).
- **Same factory interface, same gate.** `tuya_mdev_test1/2` scan to enter product
  test, `auzkey`/`uuid`/`pskKey` emitted unconditionally (`fac_pin` echoed, not
  checked), closed by the activation + 15-minute `mf_test` KV flag — a local-RF,
  not network, surface, exactly as on Beken.
- **OTA is secKey-HMAC gated** (`file hmac check success/fail`, `hmacKey:%s`,
  `secKey`) — the same per-device/session authorization as Beken.

Net: ET31 is **risk-equivalent to the patched Beken WHDZ03** — no over-the-air key-free
route; conversion is serial-only. The absence of secure boot helps only *after*
you have UART access (an arbitrary unsigned image boots), which suits a serial
flasher (LibreTiny/ltchiptool cover RTL8720CF), not a remote attack.


## Dumps studied

Public dumps from openshwprojects/BekenEmulator `firmwares/` (the upstream the
emulator already fetches S61 dumps from) and openshwprojects/FlashDumps, copied
into `captures/tuya/` (private, uncommitted). Span old SDK → newest TuyaOS:

- `BK7231T_Tuya_BN-LINK_Plug_WB2S_1.0.3` — old SDK.
- `BK7231N_Tuya_Plug_TSL-PS-F4U-01-W_1.1.17` — mid.
- `BK7231N_Tuya_NAS-PS10_PresenceSensor_…_TuyaOS_3.8.18` — TuyaOS 3.8.
- `BK7231N_Tuya_BEOK_TOL47WIFI_Thermostat_…_TuyaOS_3.11.12` — TuyaOS 3.11, the
  main subject of the disassembly above.
- BK7238 Tuya T-series dumps (the chip the repository's Beken route targets): the **TH08**, **IHSENO TempHum** and
  **KWS-306WF** devices (file version "3.0.22"; **SDK banner `TuyaOS
  V:3.11.11`**, patched) are the subject of the fac_pin / key-storage /
  leak-surface disassembly above, plus ~60 more BK7238 and ~70 BK7231N/T/Q for
  breadth. (Public copies of these are in `FlashDumps` by chip family, e.g.
  `IoT/BK7238/`.)
- `RTL8720CF_Tuya_Eightree_ET31_UKPlug_…_1.3.7` — **SDK 2.3.3 (2023 build),
  patched**; the Realtek subject above.

`bk7231tools dissect_dump` splits each Beken dump into bootloader + app RBL and
decrypts the app. The raw Beken flash is **CRC-striped** (32 data bytes + a 2-byte
CRC per 34-byte block); un-stripe to a logical image first (`tools/beken/
unstripe.py`, or simply keep bytes `[0:32]` of every 34), after which **CPU
address == logical offset** and the repo's `tools/beken/` static tools
(`disasm.py`, `xref.py`, `decomp.py` via angr) read cleanly. (BK7238's dump uses a
non-standard RBL layout `bk7231tools`' default missed; `layouts.py`/`unstripe.py`
handle it.) The **RTL8720CF** dump is **not** Beken-striped — it is an AmebaZ2
image (manifest magic `99 99 96 96` at offset 0, two OTA slots); split it with
`ltchiptool` and disassemble the code section at the load address from its image2
header (capstone ARM Thumb / Cortex-M).

**Reading the generation of a dump:** the patch status follows the **SDK**
version, not the product firmware version in the file name. The decrypted app
carries a banner — `< TuyaOS V:3.11.12 BS:40.00 PT:2.3 LAN:3.5 CAD:1.0.5
CD:1.0.0 >` on 3.x, or `TUYA IOT SDK V:x` / `TUYA IOT SDK V:2.x` on older builds
— and that `TuyaOS V:` / `TUYA IOT SDK V:` string is what decides "unpatched vs
patched". (The banner also encodes the LAN protocol version, e.g. `LAN:3.5`.)


## Supporting Tuya: the realistic shapes

Not a commitment; the options, honestly costed.

1. **Unpatched devices, via cloudcutter.** The tool detects/sorts Tuya devices,
   builds the OpenBeken/ESPHome image + pin map, and **invokes cloudcutter** for
   the exploit + flash. Real no-solder liberation, but only for old stock, and it
   inherits cloudcutter's Wi-Fi-adapter/Docker requirements. Mostly a UX and
   image-building wrapper over existing work.
2. **Patched devices, serial-assisted.** A guided UART path: open device → read
   keys (or just flash OpenBeken directly). This is a *different product* from
   the current OTA tool and overlaps heavily with existing GUI flashers
   (BK7231Flasher, ltchiptool); little reason to duplicate.
3. **Do nothing / document.** Given (2) is covered by others and (1) is covered
   by cloudcutter, the clearest contribution may be to keep this page as the
   reference for *why* Tuya is not an eWeLink-style target, and revisit only if
   the pairing-`reg` lead or a new key-recovery method changes the picture.

Recommendation: **don't build native Tuya OTA.** If Tuya support is wanted,
pursue shape 1 (a thin cloudcutter wrapper) and only for the unpatched
population; treat patched Tuya as out of scope for an OTA tool. The
[register-center override](#the-register-center-override-a-cleaner-redirect-same-blocker)
would let a native tool do a clean, DNS-spoof-free local re-onboard — but only
once the owner already holds the device's `authKey`, so it does not remove the
one-time physical key read that keeps patched devices out of a pure-OTA tool.


## Open questions and next steps (ranked)

The remote picture is settled (no over-the-air key route on patched devices); what
is left either needs a **live owned device / router capture** or a **deeper static
RE pass**. Each item says which. The **recommended first step** is #1 — it is the
only item that could still turn up a no-solder key read, so it decides whether the
residual risk is "local-RF against factory-fresh units" or "none without opening."

1. **[live device] Factory-fresh prod-test over the air — the one residual
   key-disclosure risk.** The production-test interface emits
   `authKey`/`uuid`/`pskKey` with no PIN check, closed on provisioned units by the
   activation + 15-min `mf_test` flag. _Does an **unprovisioned / just-factory-
   reset** unit auto-enter Wi-Fi production test on boot (joinable via a rogue
   `tuya_mdev_test1/4` AP with PSK `12345678`), or only on the TuyaMCU-UART
   command?_ If it auto-enters, a factory-fresh or reset unit is harvestable over
   local RF with no soldering — the only conceivable no-solder key read left. Also
   confirm `mf_test` ships closed and that factory reset does **not** reopen it.
   _Next step:_ stand up the rogue `tuya_mdev_test` AP, boot a reset WHDZ03/ET31 in
   range, and watch for association + the cJSON key reply; corroborate with an
   opnsense/router capture of the device's boot traffic.
2. **[static RE, then live] Is the KV AES key device-unique or a fixed SDK
   constant?** On both families `authKey` sits in an AES-encrypted KV blob. If the
   wrapping key is a known fixed constant, a **flash dump alone** yields `authKey`
   with no further secret — materially easing the physical route and feeding the
   cloudcutter known-secrets / reg-override flow. _Next step:_ trace the KV
   decrypt key-derivation routine (`aes128_ecb_decode_raw` / `sf_flash_encrypt_key`
   on RTL8720CF; the KV read path at `0x0d5180` on Beken) to see whether the key is
   constant, MAC/uuid-derived, or chip-OTP-backed; confirm against a live unit's
   dump.
3. **[live device] Register-center override — answered: yes in retail, still
   `authKey`-gated.** AP/BLE pairing accepts a `reg{url0,ca,…}` override (no auth);
   it redirects an owned device to a cloud of your own with no DNS spoof, but activation
   still needs the factory `authKey`. See
   [the register-center override](#the-register-center-override-a-cleaner-redirect-same-blocker).
   _Next step:_ confirm the SL value via `query_dev` and walk the factory-reset →
   re-pair-with-reg path on owned hardware (end-to-end, once `authKey` is in hand).
4. **[static RE] ET31 token-clamp at instruction level.** ET31 is patched by build
   date (cloudcutter's criterion); an instruction-level check of the `__udp_ap_v3`
   token bounds clamp, or a live cloudcutter profile attempt, would confirm it
   directly rather than by date.
5. **[static RE] BK7238 bootloader verify path.** The no-secure-boot verdict rests
   on the BK7231N 3.11 RT-Thread path and the absence of asymmetric-verify code in
   the apps; disassembling the BK7238 boot loader/ROM itself would confirm the BK7238
   has no signature check (expected, but unconfirmed at instruction level).
6. **[static RE] Is any `lan_ext_cmd_parse` extension command a reachable OTA
   trigger?** Moot for flashing (secKey-gated) but worth knowing for completeness.
7. **[live device] T3/T5 (BK7236/BK7258):** do retail units enable the Armino
   SDK's TF-M secure boot? Would foreclose even the serial route for those.

Legal footing is the the same as for the existing firmware study ([legal.md](legal.md));
Tuya adds no new barrier beyond "the owner's own device."
