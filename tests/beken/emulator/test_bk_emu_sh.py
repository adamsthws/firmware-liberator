"""bk-emu.sh hands the container its own process: a signal to the script - a
timeout's, a Ctrl-C - reaches the docker client and so the container, instead
of leaving both running behind a script that has gone."""

import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "tools" / "beken" / "emulator" / "bk-emu.sh"
IMAGE_SH = ROOT / "tools" / "beken" / "emulator" / "image.sh"
PLUG_NET = ROOT / "tools" / "beken" / "emulator" / "plug-net.sh"


@unittest.skipIf(shutil.which("bash") is None, "no bash")
class ContainerIsExecedTests(unittest.TestCase):
    def test_docker_runs_as_the_script_itself(self):
        function = re.search(r"^container\(\) \{.*?^\}", SCRIPT.read_text(encoding="utf-8"), re.S | re.M).group(0)
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp, "docker")
            fake.write_text("#!/bin/sh\necho \"docker pid $$: $*\"\n", encoding="utf-8")
            fake.chmod(0o755)
            script = ("set -u\nROOT=%s FIRMWARES=%s/firmwares IMAGE=img PUBLIC_DUMP=dump.bin\n%s\n"
                      "echo \"shell pid $$\"\ncontainer python -c pass\necho \"still here\"\n"
                      % (tmp, tmp, function))
            done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60,
                                  env={**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"}, cwd=tmp)
        self.assertEqual(done.returncode, 0, done.stderr)
        shell, docker = re.search(r"shell pid (\d+)", done.stdout), re.search(r"docker pid (\d+)", done.stdout)
        self.assertEqual(shell.group(1), docker.group(1), done.stdout)
        self.assertNotIn("still here", done.stdout)


@unittest.skipIf(shutil.which("bash") is None, "no bash")
class PlugDumpTests(unittest.TestCase):
    def dump(self, own):
        function = re.search(r"^plug_dump\(\) \{.*?^\}", SCRIPT.read_text(encoding="utf-8"), re.S | re.M).group(0)
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.realpath(tmp)
            Path(root, "captures").mkdir()
            Path(root, "captures", "device.bin").touch()
            script = f"set -u\nROOT={root} PUBLIC_DUMP=dump.bin\n{function}\nplug_dump\n"
            env = {**os.environ, "AUTOFLASH_BEKEN_EMU_FLASH": own.format(root=root)}
            if not own:
                del env["AUTOFLASH_BEKEN_EMU_FLASH"]
            return subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60, env=env, cwd=tmp)

    def test_a_new_plug_starts_from_the_public_dump(self):
        self.assertEqual(self.dump("").stdout.strip(), "/opt/beken-emulator/firmwares/dump.bin")

    def test_or_from_a_flash_under_the_checkout_as_the_container_sees_it(self):
        self.assertEqual(self.dump("{root}/captures/device.bin").stdout.strip(), "/work/captures/device.bin")

    def test_one_outside_the_checkout_is_refused(self):
        done = self.dump("/nowhere/device.bin")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("outside this checkout", done.stderr)


def english_locale():
    """A UTF-8 English locale this computer has, or None."""
    try:
        found = subprocess.run(["locale", "-a"], capture_output=True, text=True, timeout=10).stdout.split()
    except OSError:
        return None
    return next((name for name in found if name.lower() in ("en_gb.utf8", "en_us.utf8")), None)


@unittest.skipIf(shutil.which("bash") is None or english_locale() is None, "no bash or English locale")
class ImageTagTests(unittest.TestCase):
    """An emulator image's tag is the same on every computer: its files sorted
    by byte, not by the locale - an English one sorts UPSTREAM among the lower
    case names, C before them, and a laptop's tag and the published image's
    did not match (seen setting up the S61's suite on other machines)."""

    def tags(self, script):
        function = re.search(r"^tag\(\) \{.*?^\}", script.read_text(encoding="utf-8"), re.S | re.M).group(0)
        here = script.parent
        found = []
        for locale in (english_locale(), "C"):
            done = subprocess.run(["bash", "-c", f"HERE={here} ROOT={ROOT}\n{function}\ntag"], capture_output=True,
                                  text=True, timeout=60, env={**os.environ, "LC_ALL": locale, "LANG": locale})
            self.assertEqual(done.returncode, 0, done.stderr)
            found.append(done.stdout.strip())
        return found

    def test_the_beken_images_tag_is_the_same_in_any_locale(self):
        english, c = self.tags(IMAGE_SH)
        self.assertEqual(english, c)

    def test_the_espressif_images_tag_is_the_same_in_any_locale(self):
        english, c = self.tags(ROOT / "tools" / "espressif" / "emulator" / "image.sh")
        self.assertEqual(english, c)


@unittest.skipIf(shutil.which("bash") is None or shutil.which("flock") is None, "no bash or flock")
class ImageLockTests(unittest.TestCase):
    def test_the_lock_is_let_go_once_the_image_is_there(self):
        # Held on, the docker run exec'd next kept it for its whole run, and
        # every other emulator's start waited behind it (seen in the fault suite):
        # image.sh, which takes it, runs as a process of its own.
        function = re.search(r"^ensure\(\) \{.*?^\}", SCRIPT.read_text(encoding="utf-8"), re.S | re.M).group(0)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "image.sh").write_text('#!/bin/bash\nexec 9>"$(dirname "$0")/lock"\nflock 9\n',
                                             encoding="utf-8")
            Path(tmp, "image.sh").chmod(0o755)
            script = ("set -u\nHERE=%s IMAGE=img\n%s\nensure\n"
                      "[ -e /proc/$$/fd/9 ] && echo held || echo free\n" % (tmp, function))
            done = subprocess.run(["bash", "-c", script], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.stdout.strip(), "free", done.stderr)


@unittest.skipIf(shutil.which("bash") is None, "no bash")
class PlugNetRouterTests(unittest.TestCase):
    """plug-net.sh makes the container the plug's home router: .1 on the
    network, the plug's pool where Docker hands out nothing, and every
    connection the plug makes off the network sent to the stub there."""

    def commands(self, address="198.18.61.130/24", then=None):
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp, "calls")
            state = Path(tmp, "state")
            state.mkdir()
            record = '#!/bin/sh\necho "$(basename "$0") $*" >> %s\n' % calls
            fakes = {
                "ip": record + "case \"$*\" in\n"
                      "  '-4 -o address show dev eth0') echo '2: eth0    inet %s brd x scope global eth0';;\n"
                      "esac\n" % address,
                "iptables": record, "dnsmasq": record, "setpriv": record, "chown": record,
                # The stub makes its CA, which plug-net.sh waits for.
                "python3": record + "touch %s/ca.crt %s/leaf.crt %s/leaf.key\n" % (state, state, state),
            }
            for name, text in fakes.items():
                Path(tmp, name).write_text(text, encoding="utf-8")
                Path(tmp, name).chmod(0o755)
            done = subprocess.run(["bash", str(PLUG_NET)], capture_output=True, text=True, timeout=60,
                                  env={**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}",
                                       "PLUG_NET_RUN": str(Path(tmp, "run")), "PLUG_STATE": str(state),
                                       "HOST_UID": "1000", "HOST_GID": "1000"})
            if then is not None:
                Path(tmp, "run", "leases").write_text("1 12:34:56:ab:cd:ef 198.18.61.50 plug *\n", encoding="utf-8")
                calls.write_text("", encoding="utf-8")
                subprocess.run(["sh", str(Path(tmp, "run", "router")), *then], capture_output=True, timeout=30,
                               env={**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"})
                self.leases = Path(tmp, "run", "leases").read_text(encoding="utf-8")
            return done, calls.read_text(encoding="utf-8") if calls.exists() else ""

    def test_the_router_starts_again_forgetting_the_plugs_address(self):
        # As a router may, when a device restarts: the fault suite's readdress.
        done, calls = self.commands(then=["55"])
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("--dhcp-range=198.18.61.55,198.18.61.59,", calls)
        self.assertEqual(self.leases, "")

    def test_the_container_is_the_plugs_router_and_cloud(self):
        done, calls = self.commands()
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("ip address add 198.18.61.1/24 dev br0", calls)
        dnsmasq = next(line for line in calls.splitlines() if line.startswith("dnsmasq "))
        self.assertIn("--dhcp-range=198.18.61.50,198.18.61.59,", dnsmasq)
        self.assertIn("--dhcp-option=option:router,198.18.61.1", dnsmasq)
        self.assertIn("--address=/#/198.18.61.1", dnsmasq)
        self.assertIn("-s 198.18.61.48/28 ! -d 198.18.61.0/24 -j DNAT --to-destination 198.18.61.1", calls)
        self.assertIn("--bind 198.18.61.1", calls)

    def test_a_network_not_a_24_is_refused(self):
        done, calls = self.commands("10.0.0.5/16")
        self.assertNotEqual(done.returncode, 0)
        self.assertNotIn("br0", calls)


@unittest.skipIf(shutil.which("bash") is None, "no bash")
class NetworkRaceTests(unittest.TestCase):
    """Plugs started side by side may choose the same free subnet: the one
    whose create loses chooses again (seen with eight at once on a VM)."""

    def make(self, creates_fail):
        function = re.search(r"^network\(\) \{.*?^\}", SCRIPT.read_text(encoding="utf-8"), re.S | re.M).group(0)
        with tempfile.TemporaryDirectory() as tmp:
            calls = Path(tmp, "calls")
            docker = ("#!/bin/sh\necho \"$*\" >> %s\n"
                      "case \"$1 $2\" in\n"
                      "  'network inspect') [ -f %s/made ] ;;\n"
                      "  'network ls') : ;;\n"
                      "  'network create') n=$(grep -c 'network create' %s); [ \"$n\" -gt %d ] && touch %s/made ;;\n"
                      "esac\n" % (calls, tmp, calls, creates_fail, tmp))
            for name, text in {"docker": docker, "sleep": "#!/bin/sh\n", "ip": "#!/bin/sh\n"}.items():
                Path(tmp, name).write_text(text, encoding="utf-8")
                Path(tmp, name).chmod(0o755)
            done = subprocess.run(["bash", "-c", "set -u\nNETWORK=net\n%s\nnetwork" % function],
                                  capture_output=True, text=True, timeout=60,
                                  env={**os.environ, "PATH": f"{tmp}:{os.environ['PATH']}"})
            return done, calls.read_text(encoding="utf-8").count("network create")

    def test_a_lost_race_is_tried_again(self):
        done, creates = self.make(creates_fail=2)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(creates, 3)

    def test_it_gives_up_in_the_end(self):
        done, creates = self.make(creates_fail=99)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("Could not make the Docker network net", done.stderr)


def checkout(tmp):
    """What bk-emu.sh needs of this checkout, copied under `tmp`."""
    for name in ("tools/beken/emulator/bk-emu.sh", "tools/beken/emulator/folders.py",
                 "tools/beken/emulator/image.sh", "tools/beken/emulator/UPSTREAM", "tools/repo/emulator-room.sh"):
        Path(tmp, name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, Path(tmp, name))


@unittest.skipIf(shutil.which("bash") is None, "no bash")
class OptionTests(unittest.TestCase):
    """bk-emu.sh spells the jobs it shares with emu.sh as emu.sh does:
    --instance N, --fresh, --rebuild."""

    def run_script(self, *args, before=None):
        """bk-emu.sh ARGS, run from a copy of its checkout in a temporary
        directory, so what it does to captures/ is done there; Docker a fake
        that says what it was asked."""
        with tempfile.TemporaryDirectory() as tmp:
            checkout(tmp)
            calls = Path(tmp, "calls")
            Path(tmp, "bin").mkdir()
            Path(tmp, "bin", "docker").write_text('#!/bin/sh\necho "$*" >> %s\n' % calls, encoding="utf-8")
            Path(tmp, "bin", "docker").chmod(0o755)
            if before:
                before(Path(tmp))
            done = subprocess.run(["bash", str(Path(tmp, SCRIPT.relative_to(ROOT))), *args], capture_output=True,
                                  text=True, timeout=60, env={**os.environ, "PATH": f"{tmp}/bin:{os.environ['PATH']}"})
            self.left = sorted(str(path.relative_to(tmp)) for path in Path(tmp, "captures").rglob("*"))
            return done, calls.read_text(encoding="utf-8") if calls.exists() else ""

    def test_an_instance_is_its_own_plug(self):
        done, calls = self.run_script("--instance", "2", "ui")
        self.assertEqual(done.returncode, 1, done.stderr)
        self.assertIn("exec autoflash-beken-emu-2 ", calls)

    def test_an_instance_out_of_range_is_refused(self):
        done, calls = self.run_script("--instance", "31", "plug")
        self.assertEqual(done.returncode, 2)
        self.assertIn("1-30", done.stderr)
        self.assertEqual(calls, "")

    def test_fresh_is_for_plug_alone(self):
        done, calls = self.run_script("--fresh", "power", "on")
        self.assertEqual(done.returncode, 2)
        self.assertIn("--fresh is for plug", done.stderr)
        self.assertEqual(calls, "")

    def test_the_old_spellings_are_gone(self):
        for args in (("reset",), ("ensure", "--rebuild"), ("--bogus", "plug")):
            done, calls = self.run_script(*args)
            self.assertEqual(done.returncode, 2, args)
            self.assertIn("--instance N", done.stdout, args)
            self.assertEqual(calls, "", args)

    def test_an_earlier_checkouts_folders_are_moved_on_first_use(self):
        def old(tmp):
            Path(tmp, "captures/emulation/s61/autoflash-s61-2").mkdir(parents=True)
            Path(tmp, "captures/emulation/s61/autoflash-s61-2/flash.bin").write_bytes(b"plug")
        done, _calls = self.run_script("--instance", "2", "ui", before=old)
        self.assertIn("s61/autoflash-s61-2 to beken/state-2", done.stderr)
        self.assertEqual(self.left, ["captures/emulation", "captures/emulation/beken",
                                     "captures/emulation/beken/state-2", "captures/emulation/beken/state-2/flash.bin"])

    def test_rebuild_reaches_image_sh(self):
        function = re.search(r"^ensure\(\) \{.*?^\}", SCRIPT.read_text(encoding="utf-8"), re.S | re.M).group(0)
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "image.sh").write_text('#!/bin/sh\necho "$*"\n', encoding="utf-8")
            Path(tmp, "image.sh").chmod(0o755)
            found = [subprocess.run(["bash", "-c", f"set -u\nHERE={tmp} IMAGE=img rebuild={rebuild}\n{function}\nensure"],
                                    capture_output=True, text=True, timeout=60).stdout.strip()
                     for rebuild in ("--rebuild", '""')]
        self.assertEqual(found, ["ensure img --rebuild", "ensure img"])


if __name__ == "__main__":
    unittest.main()
