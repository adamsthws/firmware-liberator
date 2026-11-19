#!/usr/bin/env python3
"""A converted device in Home Assistant and the ESPHome builder, end to end, on
its emulator: what docs/home-assistant-adoption.md and each device's
esphome.md say a person finds. Run by hand before a release (docs/testing.md),
never in CI: it pulls Home Assistant and builds firmware, which takes minutes.

    .venv/bin/python -I tools/repo/home_assistant_check.py espressif|beken --instance N [--from NAME]

The family's device (an S60, an S61) starts on this project's ESPHome from the
fault suite's saved start (--from another) as emulator N, with a throwaway
Home Assistant and builder beside it on its Wi-Fi. In turn:

    discovered   Home Assistant finds the device itself (zeroconf) and adds it,
                 with a made-up user and no key
    switched     its entities come through, and the relay switches with the
                 power reading following it
    offered      the builder offers to adopt it - an S60; an S61 is not offered
                 (NOT_OFFERED says why), and the check notices if it is
    adopted      adopting it writes the config devices/sonoff-s61/esphome.md shows
    installed    the builder builds that config and installs it over the air,
                 and Home Assistant carries on with the device
    refused      the tool leaves the device alone, a build of its own now
    replaced     --replace-own-build puts this project's build back, and Home
                 Assistant carries on again

Each step is printed PASS or FAIL as it ends, and the check stops at the first
FAIL: the rest need it. What it saw is kept in
captures/emulation/home-assistant/<family>-<N>/. Its containers
(home-assistant-check-N, esphome-check-N) and the emulator are stopped by name,
whatever happens.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.autoflash import records  # noqa: E402

# The Home Assistant release the check runs, the one devices/*/esphome.md name
# as tried: moved on by hand, with them, when a newer one is tried. The
# builder is the ESPHome image the device's published build came from
# (artifacts/firmware.json's "builder"), so the build it makes is that one's.
HOME_ASSISTANT = "ghcr.io/home-assistant/home-assistant:2026.11.2"
HA_PORT, BUILDER_PORT = 8123, 6052
# Home Assistant's image is some 2.5 GB, and the rest of the check writes
# little to disk: the builder's toolchain, some 3 GB, is in memory.
FREE_DISK_GB = 4
BUILDER_MEMORY = "6g"
# A made-up Home Assistant user for the throwaway instance.
USER = {"name": "Test Person", "username": "tester", "password": "made-up-password-1", "language": "en"}

FAMILIES = {
    "espressif": {"device": "sonoff-s60", "start": "stub-suite-esphome", "offered": True},
    "beken": {"device": "sonoff-s61", "start": "esphome", "offered": False},
}
# Why the builder does not offer an S61 (devices/sonoff-s61/esphome.md): its
# ESPHome runs on LibreTiny, whose mDNS (lwIP, MDNS_LABEL_MAXLEN) takes no TXT
# item over 63 bytes, and package_import_url is longer.
NOT_OFFERED = "LibreTiny's mDNS takes no TXT item over 63 bytes, and package_import_url is longer"
TXT_ITEM_LIMIT = 63

DISCOVERED_WITHIN = 300     # seconds for Home Assistant's zeroconf to find the device
FOLLOWS_WITHIN = 60         # seconds for the power reading to follow the relay
OFFERED_WITHIN = 120        # seconds for the builder to have heard the device announce itself
BUILT_WITHIN = 3600         # seconds for a build: the first fetches the toolchain
RESTART_SEEN_WITHIN = 90    # seconds for Home Assistant to see the device go, as it restarts
BACK_WITHIN = 300           # seconds for it to have the device back
OFF_BELOW, ON_ABOVE = 0.5, 1.0   # watts: the relay off reads none, on its load


# -- what the check expects, from the repository ------------------------------

def import_url(device: str) -> str:
    """The config the device says it came from (its esphome.yaml's
    dashboard_import), which adopting it points the builder's config at."""
    text = (ROOT / "devices" / device / "esphome.yaml").read_text(encoding="utf-8")
    found = re.search(r"^dashboard_import:\n(?:  .*\n)*?  package_import_url: (\S+)", text, re.M)
    if not found:
        raise SystemExit(f"devices/{device}/esphome.yaml names no package_import_url")
    return found.group(1)


def announced_whole(device: str) -> bool:
    """Whether the device can announce its package_import_url in one TXT item."""
    return len(f"package_import_url={import_url(device)}") <= TXT_ITEM_LIMIT


def builder_image(family: str, device: str) -> str:
    pins = json.loads((ROOT / "artifacts/firmware.json").read_text(encoding="utf-8"))
    return pins[family][device]["esphome"]["builder"]


def documented_config(device: str, name: str) -> str:
    """The config devices/sonoff-s61/esphome.md shows the builder writing on
    adopting a device ("Adding it by hand"), for this device and name: the
    same for every device but its folder."""
    page = (ROOT / "devices/sonoff-s61/esphome.md").read_text(encoding="utf-8")
    found = re.search(r"\*\*Adding it by hand\.\*\*.*?```yaml\n(.*?)```", page, re.S)
    if not found:
        raise SystemExit("devices/sonoff-s61/esphome.md shows no config under 'Adding it by hand'")
    example = re.search(r"name: (esphome-\w+)", found.group(1)).group(1)
    text = found.group(1).replace("sonoff-s61", device).replace(example, name)
    return text.replace(example.replace("-", " "), name.replace("-", " "))


def config_lines(text: str) -> list[str]:
    """A YAML config's lines as they mean: no blank or comment lines, no
    trailing space, a quoted value unquoted."""
    lines = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        lines.append(re.sub(r""": (["'])(.*)\1$""", r": \2", line))
    return lines


def config_differences(written: str, documented: str) -> list[str]:
    """What the builder wrote that the page does not show, and the other way."""
    wrote, shown = config_lines(written), config_lines(documented)
    return ([f"written, not shown: {line.strip()}" for line in wrote if line not in shown]
            + [f"shown, not written: {line.strip()}" for line in shown if line not in wrote])


# -- what Home Assistant and the builder say -----------------------------------

def esphome_flow(progress: list[dict]) -> dict | None:
    """The ESPHome flow zeroconf started for a device, among those in progress."""
    return next((flow for flow in progress
                 if flow.get("handler") == "esphome" and flow.get("context", {}).get("source") == "zeroconf"), None)


def device_name(flow: dict) -> str:
    """The device's name on the network, esphome- and its MAC's last six
    characters, from the flow Home Assistant's zeroconf started for it."""
    context = flow.get("context", {})
    found = re.search(r"\b(esphome-[0-9a-f]{6})\b", context.get("title_placeholders", {}).get("name", ""))
    if found:
        return found.group(1)
    mac = re.sub(r"[^0-9a-f]", "", str(context.get("unique_id", "")).lower())
    if len(mac) != 12:
        raise ValueError(f"no device name in {context}")
    return f"esphome-{mac[-6:]}"


def device_entities(registry: list[dict], entry_id: str) -> list[str]:
    return sorted(entity["entity_id"] for entity in registry if entity.get("config_entry_id") == entry_id)


def available(states: list[dict]) -> list[dict] | None:
    """A device's entities' states, once there are some and none is unavailable."""
    return states if states and all(state["state"] != "unavailable" for state in states) else None


def relay_and_power(states: list[dict]) -> tuple[str | None, str | None]:
    """The relay (a switch named so, else the only switch) and the power
    reading among a device's entities' states."""
    switches = [s["entity_id"] for s in states if s["entity_id"].startswith("switch.")]
    relay = next((s for s in switches if s.endswith("_relay")), switches[0] if len(switches) == 1 else None)
    power = next((s["entity_id"] for s in states if s["entity_id"].startswith("sensor.")
                  and s.get("attributes", {}).get("device_class") == "power"), None)
    return relay, power


def reading(state: dict | None) -> float | None:
    try:
        return float(state["state"])
    except (TypeError, KeyError, ValueError):
        return None


def offer_for(listed: dict | None, name: str) -> dict | None:
    """The builder's offer to adopt a device by its name, in devices/list."""
    return next((device for device in (listed or {}).get("importable", []) if device.get("name") == name), None)


def finished_job(jobs) -> dict | None:
    """The builder's newest job, once it has ended."""
    jobs = jobs if isinstance(jobs, list) else (jobs or {}).get("jobs", [])
    if not jobs:
        return None
    newest = max(jobs, key=lambda job: job.get("created_at") or "")
    return None if newest.get("status") in ("queued", "pending", "running") else newest


def words(said: str) -> str:
    """What a run said, its screen's colours, line breaks and box edges
    gone, so a message wrapped there is found whole."""
    return " ".join(re.sub(r"\x1b\[[0-9;]*m|[│║┃]", " ", said).split())


# -- a WebSocket, as Home Assistant's API and the builder's are ----------------

class WebSocket:
    """A text WebSocket client, enough for Home Assistant's and the builder's
    JSON messages (RFC 6455: masked frames out, pings answered)."""

    def __init__(self, sock: socket.socket):
        self.sock = sock

    @classmethod
    def open(cls, host: str, port: int, path: str, timeout: float = 120) -> WebSocket:
        sock = socket.create_connection((host, port), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
                      f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
                     .encode())
        answer = b""
        while b"\r\n\r\n" not in answer:
            got = sock.recv(1)
            if not got:
                sock.close()
                raise ConnectionError(f"{path}: closed before its upgrade")
            answer += got
        status = answer.split(b"\r\n", 1)[0].decode(errors="replace")
        if " 101 " not in status:
            sock.close()
            raise ConnectionError(f"{path}: {status}")
        return cls(sock)

    def close(self) -> None:
        self.sock.close()

    def frame(self, opcode: int, data: bytes) -> None:
        mask = os.urandom(4)
        n = len(data)
        if n < 126:
            length = bytes([0x80 | n])
        elif n < 65536:
            length = bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            length = bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(bytes([0x80 | opcode]) + length + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, message: dict) -> None:
        self.frame(0x1, json.dumps(message).encode())

    def exact(self, n: int) -> bytes:
        got = b""
        while len(got) < n:
            chunk = self.sock.recv(n - len(got))
            if not chunk:
                raise ConnectionError("closed")
            got += chunk
        return got

    def receive(self) -> dict:
        message = b""
        while True:
            first, second = self.exact(2)
            n = second & 0x7F
            if n == 126:
                n = struct.unpack(">H", self.exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self.exact(8))[0]
            payload = self.exact(n)
            opcode = first & 0x0F
            if opcode == 0x9:
                self.frame(0xA, payload)
            elif opcode == 0x8:
                raise ConnectionError("closed by the server")
            elif opcode in (0x0, 0x1):
                message += payload
                if first & 0x80:
                    return json.loads(message)


class HomeAssistant:
    """The throwaway Home Assistant's REST and WebSocket APIs."""

    def __init__(self, host: str):
        self.host = host
        self.base = f"http://{host}:{HA_PORT}"
        self.token = ""
        self.ws: WebSocket | None = None
        self.number = 0

    def rest(self, method: str, path: str, data=None, form: bool = False):
        headers, body = {}, None
        if data is not None:
            if form:
                body = urllib.parse.urlencode(data).encode()
                headers["Content-Type"] = "application/x-www-form-urlencoded"
            else:
                body = json.dumps(data).encode()
                headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(self.base + path, data=body, method=method, headers=headers)
        with urllib.request.urlopen(request, timeout=60) as answer:
            text = answer.read().decode()
        return json.loads(text) if text else None

    def onboard(self, within: float = 600) -> None:
        """A made-up user, a token that outlasts a build, and onboarding done,
        as a person's first visit."""
        steps = until("Home Assistant never answered", within, lambda: self.rest("GET", "/api/onboarding"))
        if any(step["step"] == "user" and step["done"] for step in steps):
            raise Failed("Home Assistant was onboarded already: not a throwaway one")
        client = self.base + "/"
        code = self.rest("POST", "/api/onboarding/users", {"client_id": client, **USER})["auth_code"]
        self.token = self.rest("POST", "/auth/token", {"grant_type": "authorization_code", "code": code,
                                                         "client_id": client}, form=True)["access_token"]
        for step, data in (("core_config", {}), ("analytics", {}),
                           ("integration", {"client_id": client, "redirect_uri": client})):
            self.rest("POST", f"/api/onboarding/{step}", data)
        # The first token lasts half an hour, less than a first build.
        self.token = self.command({"type": "auth/long_lived_access_token", "client_name": "home_assistant_check",
                                   "lifespan": 1})
        self.close()

    def every_adapter(self) -> bool:
        """Zeroconf on every network the container is on, not only the one its
        default route takes: an S60's Wi-Fi is the computer's other one. True
        if that changed it, which takes a restart."""
        network = self.command({"type": "network"})
        every = sorted(adapter["name"] for adapter in network["adapters"])
        if sorted(network.get("configured_adapters") or []) == every:
            return False
        self.command({"type": "network/configure", "config": {"configured_adapters": every}})
        self.close()
        return True

    def wait_up(self, within: float = 300) -> None:
        time.sleep(10)   # gone, as it restarts
        until("Home Assistant did not come back", within, lambda: self.rest("GET", "/api/") is not None)

    def command(self, message: dict):
        """One WebSocket command's result."""
        if self.ws is None:
            self.ws = WebSocket.open(self.host, HA_PORT, "/api/websocket")
            self.ws.receive()   # auth_required
            self.ws.send({"type": "auth", "access_token": self.token})
            if self.ws.receive().get("type") != "auth_ok":
                raise ConnectionError("Home Assistant refused the token")
        self.number += 1
        try:
            self.ws.send({**message, "id": self.number})
            while True:
                got = self.ws.receive()
                if got.get("id") == self.number and got.get("type") == "result":
                    break
        except OSError:
            self.close()
            raise
        if not got.get("success"):
            raise RuntimeError(f"{message['type']}: {got.get('error')}")
        return got.get("result")

    def close(self) -> None:
        if self.ws:
            self.ws.close()
            self.ws = None

    def state(self, entity: str) -> dict | None:
        try:
            return self.rest("GET", f"/api/states/{entity}")
        except urllib.error.HTTPError:
            return None

    def switch(self, entity: str, on: bool) -> None:
        self.rest("POST", f"/api/services/switch/turn_{'on' if on else 'off'}", {"entity_id": entity})


class Builder:
    """The throwaway ESPHome builder's command API (/ws)."""

    def __init__(self, host: str):
        self.host = host

    def command(self, command: str, args: dict, within: float = 300):
        ws = WebSocket.open(self.host, BUILDER_PORT, "/ws", timeout=within)
        try:
            ws.receive()   # its hello
            ws.send({"command": command, "message_id": "1", "args": args})
            deadline = time.monotonic() + within
            while time.monotonic() < deadline:
                got = ws.receive()
                if got.get("message_id") != "1" or "event" in got:
                    continue
                if got.get("error"):
                    raise RuntimeError(f"{command}: {got['error']}")
                return got.get("result")
            raise TimeoutError(f"{command}: no answer within {within:.0f} s")
        finally:
            ws.close()


# -- the containers ------------------------------------------------------------

def docker(*args: str, check: bool = True, timeout: float = 600) -> str:
    done = subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout)
    if check and done.returncode:
        raise RuntimeError(f"docker {' '.join(args[:2])}: {done.stderr.strip()[-400:]}")
    return done.stdout.strip()


def address_of(container: str, network: str) -> str:
    return docker("inspect", "-f", f'{{{{(index .NetworkSettings.Networks "{network}").IPAddress}}}}', container)


def gone(*names: str) -> None:
    """These containers removed, with their anonymous volumes (a /config each)."""
    for name in names:
        docker("rm", "-f", "-v", name, check=False, timeout=120)


def start_container(name: str, image: str, network: str, *options: str, command: tuple[str, ...] = ()) -> None:
    """A container on `network` ("container:NAME" shares that one's)."""
    gone(name)
    docker("run", "-d", "--name", name, "--network", network, "-e", "TZ=UTC", *options, image, *command,
           timeout=1800)


def start_builder(name: str, image: str, network: str, secrets: str) -> None:
    """The builder, its /config in memory: its first build fetches a toolchain
    of some 3 GB, which a computer short of disk would run out of, and
    which goes with it. Its secrets.yaml is written as it starts: it reads
    one only then."""
    start_container(name, image, network, f"--tmpfs=/config:rw,exec,size={BUILDER_MEMORY}", "-e",
                    f"SECRETS={secrets}", "--entrypoint", "sh",
                    command=("-c", 'printf "%s" "$SECRETS" > /config/secrets.yaml && '
                                   "exec /entrypoint.sh dashboard /config"))


def free_disk() -> float:
    """GB free where Docker keeps images and containers."""
    root = docker("info", "-f", "{{.DockerRootDir}}")
    return shutil.disk_usage(root if os.path.exists(root) else "/").free / 1e9


# -- each family's emulator ----------------------------------------------------

class Espressif:
    """An S60 (tools/espressif/emulator/): its Wi-Fi is inside the emulator's
    container, the computer's, so Home Assistant and the builder share that
    container's network, as programs on the computer on the device's Wi-Fi."""

    SCRIPT = ROOT / "tools/espressif/emulator/unattended-test.sh"

    def __init__(self, n: int, start: str, out: Path):
        from tools.espressif.emulator import sanitise_device
        self.n, self.start_from, self.out = n, start, out
        self.name = f"autoflash-emu-{n}"
        self.network = f"container:{self.name}"
        self.wifi = (sanitise_device.EMULATOR_WIFI, "")   # its open network
        # The emulator's own Docker network (emu.sh), where this check reaches it.
        self.docker_network = os.environ.get("AUTOFLASH_EMU_NETWORK", "autoflash-emu")

    def run(self, *args: str, log: str) -> str:
        done = subprocess.run([str(self.SCRIPT), f"--instance={self.n}", "--stub-cloud", "--keep", "--no-api", *args],
                              capture_output=True, text=True, timeout=1800)
        said = done.stdout + done.stderr
        (self.out / log).write_text(said, encoding="utf-8")
        return said

    def start(self) -> None:
        # A run from the saved start, which leaves the emulator up (--keep), as
        # the device it was saved from: emu.sh asks which where there are several.
        from tools.repo import fault_verdicts
        saved = records.CAPTURES / "emulation/saved" / self.start_from
        devices = [d.name for d in fault_verdicts.records_under(saved).iterdir() if d.name != "unidentified"]
        if len(devices) != 1:
            raise Failed(f"{saved} is not one device's saved start")
        self.run(f"--from={self.start_from}", f"--device={devices[0].replace('-', ':')}", "--", "--firmware", "esphome",
                 log="start.log")
        if not docker("ps", "-q", "-f", f"name=^{self.name}$"):
            raise Failed(f"{self.name} did not start: see {self.out / 'start.log'}")
        # The computer's firewall (ufw) lets in only what the emulator needs:
        # this check reaches the two programs it adds from outside.
        for port in (HA_PORT, BUILDER_PORT):
            docker("exec", self.name, "ufw", "allow", "in", "on", "eth0", "to", "any", "port", str(port),
                   "proto", "tcp", check=False)

    def host(self, _beside: str) -> str:
        """Where this check reaches Home Assistant and the builder: the
        computer's own address."""
        return address_of(self.name, self.docker_network)

    def tool(self, *args: str, log: str) -> str:
        return self.run("--", "--firmware", "esphome", *args, log=log)

    def stop(self) -> None:
        docker("stop", self.name, check=False, timeout=120)


class Beken:
    """An S61 (tools/beken/emulator/): its Wi-Fi is a Docker network of its own,
    which Home Assistant and the builder join, as computers on it."""

    def __init__(self, n: int, start: str, out: Path):
        from tools.beken.emulator import fault_suite
        self.suite = fault_suite
        self.instance = fault_suite.Instance(n)
        self.start_from, self.out = start, out
        self.network = self.instance.network
        account = dict(line.split("=", 1) for line in fault_suite.ACCOUNT.splitlines() if "=" in line)
        self.wifi = (account["AUTOFLASH_WIFI_SSID"], account["AUTOFLASH_WIFI_PASSWORD"])

    def start(self) -> None:
        self.instance.reset(self.start_from)
        if not self.instance.start("esphome"):
            raise Failed(f"{self.instance.device} never answered")

    def host(self, beside: str) -> str:
        """Where this check reaches a container on the device's network."""
        return address_of(beside, self.network)

    def tool(self, *args: str, log: str) -> str:
        path = self.out / log
        path.unlink(missing_ok=True)
        run = self.instance.tool("esphome", path, more=args)
        run.wait(timeout=self.suite.RUN_LIMIT)
        return path.read_text(encoding="utf-8", errors="replace")

    def stop(self) -> None:
        self.instance.stop()
        docker("network", "rm", self.network, check=False)


EMULATORS = {"espressif": Espressif, "beken": Beken}


# -- the check -----------------------------------------------------------------

class Failed(Exception):
    pass


def until(what: str, within: float, probe, every: float = 5):
    """`probe`'s first true answer, or Failed naming `what`."""
    deadline = time.monotonic() + within
    while True:
        try:
            got = probe()
        except (OSError, ConnectionError, RuntimeError, ValueError):
            got = None
        if got:
            return got
        if time.monotonic() > deadline:
            raise Failed(f"{what}, within {within:.0f} s")
        time.sleep(every)


STEPS = ("discovered", "switched", "offered", "adopted", "installed", "refused", "replaced")


class Check:
    def __init__(self, family: str, n: int, start: str | None):
        self.family, self.n = family, n
        self.kind = FAMILIES[family]
        self.device = self.kind["device"]
        self.out = records.CAPTURES / "emulation/home-assistant" / f"{family}-{n}"
        self.emulator = EMULATORS[family](n, start or self.kind["start"], self.out)
        self.ha_name, self.builder_name = f"home-assistant-check-{n}", f"esphome-check-{n}"
        self.ha: HomeAssistant | None = None
        self.builder: Builder | None = None
        self.entry = self.relay = self.power = self.name = ""

    def say(self, verdict: str, step: str, detail: str) -> None:
        print(f"{verdict:<5} {step}: {detail}", flush=True)
        with open(self.out / "verdicts.txt", "a", encoding="utf-8") as kept:
            kept.write(f"{verdict} {step}: {detail}\n")

    def step(self, name: str, run) -> None:
        try:
            detail = run()
        except Failed as failed:
            self.say("FAIL", name, str(failed))
            raise
        except Exception as error:
            self.say("FAIL", name, f"{type(error).__name__}: {error}")
            raise Failed(name) from error
        self.say("PASS", name, detail)

    def follows(self, on: bool) -> float:
        """The relay switched on or off from Home Assistant, and the power
        reading it comes to."""
        wanted = "on" if on else "off"

        def probe():
            if (self.ha.state(self.relay) or {}).get("state") != wanted:
                self.ha.switch(self.relay, on)
                return None
            watts = reading(self.ha.state(self.power))
            return (watts,) if watts is not None and (watts > ON_ABOVE if on else watts < OFF_BELOW) else None
        return until(f"power did not follow the relay {wanted}", FOLLOWS_WITHIN, probe, every=3)[0]

    def back(self, what: str) -> None:
        """Home Assistant has the device again, once it has restarted, and
        switches it."""
        try:
            until("", RESTART_SEEN_WITHIN, lambda: (self.ha.state(self.relay) or {}).get("state") == "unavailable")
        except Failed:
            pass   # back already, or never seen gone
        until(f"Home Assistant never had the device back {what}", BACK_WITHIN,
              lambda: (self.ha.state(self.relay) or {}).get("state") in ("on", "off"))
        self.follows(False)
        self.follows(True)

    def entity_states(self) -> list[dict]:
        registry = self.ha.command({"type": "config/entity_registry/list"})
        return [state for state in (self.ha.state(e) for e in device_entities(registry, self.entry)) if state]

    # The steps, in STEPS' order.

    def discovered(self) -> str:
        flow = until("no ESPHome device found by zeroconf", DISCOVERED_WITHIN,
                     lambda: esphome_flow(self.ha.command({"type": "config_entries/flow/progress"})))
        self.name = device_name(flow)
        added = self.ha.rest("POST", f"/api/config/config_entries/flow/{flow['flow_id']}", {})
        if added.get("type") != "create_entry":
            raise Failed(f"adding it asked for more: {added.get('step_id')} {added.get('errors')}")
        self.entry = added["result"]["entry_id"]
        return f"{self.name} found and added, no key asked"

    def switched(self) -> str:
        states = until("no entities, or some unavailable", 120, lambda: available(self.entity_states()))
        self.relay, self.power = relay_and_power(states)
        if not (self.relay and self.power):
            raise Failed(f"no relay or power reading among {[s['entity_id'] for s in states]}")
        off, on = self.follows(False), self.follows(True)
        return f"{len(states)} entities; {self.relay} off {off:.1f} W, on {on:.1f} W"

    def offered(self) -> str:
        if self.kind["offered"]:
            offer = until(f"{self.name} not offered for adoption", OFFERED_WITHIN,
                          lambda: offer_for(self.builder.command("devices/list", {}), self.name), every=10)
            if offer.get("package_import_url") != import_url(self.device):
                raise Failed(f"offered from {offer.get('package_import_url')}, not {import_url(self.device)}")
            return f"offered, from {offer['package_import_url']}"
        time.sleep(OFFERED_WITHIN)   # long enough for an offer to have come
        if offer_for(self.builder.command("devices/list", {}), self.name) or announced_whole(self.device):
            raise Failed(f"offered now: devices/{self.device}/esphome.md says it is not, as {NOT_OFFERED} - "
                         "correct it, and FAMILIES")
        return f"not offered, as expected: {NOT_OFFERED}"

    def adopted(self) -> str:
        self.builder.command("devices/import", {
            "name": self.name, "friendly_name": self.name.replace("-", " "),
            "project_name": f"liberator.{self.device}", "package_import_url": import_url(self.device)})
        written = docker("exec", self.builder_name, "cat", f"/config/{self.name}.yaml")
        (self.out / f"{self.name}.yaml").write_text(written + "\n", encoding="utf-8")
        differences = config_differences(written, documented_config(self.device, self.name))
        if differences:
            raise Failed("not the config devices/sonoff-s61/esphome.md shows: " + "; ".join(differences))
        return f"{self.name}.yaml, as devices/sonoff-s61/esphome.md shows"

    def installed(self) -> str:
        self.builder.command("firmware/install", {"configuration": f"{self.name}.yaml"}, within=120)
        job = until("the build and install never ended", BUILT_WITHIN,
                    lambda: finished_job(self.builder.command("firmware/get_jobs", {}, within=60)), every=30)
        (self.out / "install.json").write_text(json.dumps(job, indent=1) + "\n", encoding="utf-8")
        if job.get("exit_code") != 0:
            raise Failed(f"the builder's job ended {job.get('status')}, exit {job.get('exit_code')}: "
                         f"{self.out / 'install.json'}")
        self.back("after the install")
        return "built and installed over the air; Home Assistant switches it still"

    def refused(self) -> str:
        if "It is a build of its own" not in words(self.emulator.tool(log="refused.log")):
            raise Failed(f"the tool did not see a build of its own: {self.out / 'refused.log'}")
        return "the tool leaves it alone: a build of its own"

    def replaced(self) -> str:
        # The screen the run ends on, which its report is: from the build of
        # its own, now this project's.
        said = words(self.emulator.tool("--replace-own-build", log="replaced.log"))
        if "Update complete!" not in said or "From: Its own build" not in said:
            raise Failed(f"no 'Update complete!' from 'Its own build': {self.out / 'replaced.log'}")
        self.back("after --replace-own-build")
        return "this project's build back; Home Assistant switches it still"

    def run(self) -> bool:
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "verdicts.txt").write_text("", encoding="utf-8")
        print(f"{self.family} emulator {self.n}, kept in {self.out}", flush=True)
        try:
            self.step("started", self.started)
            for name in STEPS:
                self.step(name, getattr(self, name))
            return True
        except Failed:
            return False
        finally:
            self.leave()

    def leave(self) -> None:
        """Nothing of the check's left running, whatever stopped it: the
        containers' logs kept first, as far as there is room for them."""
        try:
            for name in (self.ha_name, self.builder_name):
                logs = subprocess.run(["docker", "logs", name], capture_output=True, text=True)
                (self.out / f"{name}.log").write_text(logs.stdout + logs.stderr, encoding="utf-8")
        except OSError as error:
            print(f"Their logs not kept: {error}", flush=True)
        finally:
            if self.ha:
                self.ha.close()
            gone(self.ha_name, self.builder_name)
            self.emulator.stop()

    def started(self) -> str:
        """The device, Home Assistant onboarded, and the builder, all up."""
        pulled = subprocess.run(["docker", "image", "inspect", HOME_ASSISTANT], capture_output=True).returncode == 0
        needed, free = (1 if pulled else FREE_DISK_GB), free_disk()
        if free < needed:
            raise Failed(f"{free:.1f} GB free for Docker, under the {needed} GB this check needs")
        self.emulator.start()
        start_container(self.ha_name, HOME_ASSISTANT, self.emulator.network)
        self.ha = HomeAssistant(self.emulator.host(self.ha_name))
        self.ha.onboard()
        if self.ha.every_adapter():
            docker("restart", self.ha_name)
            self.ha.wait_up()
        ssid, password = self.emulator.wifi
        image = builder_image(self.family, self.device)
        start_builder(self.builder_name, image, self.emulator.network,
                      f'wifi_ssid: "{ssid}"\nwifi_password: "{password}"\n')
        self.builder = Builder(self.emulator.host(self.builder_name))
        until("the builder never answered", 300, lambda: self.builder.command("devices/list", {}) is not None)
        return f"{HOME_ASSISTANT.rsplit('/', 1)[1]}, {image.split('@')[0].rsplit('/', 1)[1]}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("family", choices=sorted(FAMILIES))
    parser.add_argument("--instance", type=int, required=True, help="the emulator's number, 1-99")
    parser.add_argument("--from", dest="start", help="the saved start, if not the fault suite's")
    args = parser.parse_args(argv)
    if not 1 <= args.instance <= 99:
        parser.error("--instance needs a number, 1-99")
    return 0 if Check(args.family, args.instance, args.start).run() else 1


if __name__ == "__main__":
    sys.exit(main())
