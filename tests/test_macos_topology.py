"""A fresh private topology response when macOS hides the child's ARP table."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest import mock

from tools.autoflash import hostsys
from tools import macos_topology

ARP = '? (192.0.2.50) at 12:34:56:ab:cd:ef on en0 ifscope [ethernet]\n'


class MacTopologyTests(unittest.TestCase):
    def table_with_peer(self, response):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'reader.sock')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
                server.bind(path)
                server.listen()
                server.settimeout(0.2)
                stop = threading.Event()

                def serve():
                    while not stop.is_set():
                        try:
                            connection, _ = server.accept()
                        except socket.timeout:
                            continue
                        with connection:
                            connection.sendall(response)
                        break

                worker = threading.Thread(target=serve)
                worker.start()
                try:
                    with mock.patch.dict(os.environ, {'AUTOFLASH_MACOS_ARP_SOCKET': path}), \
                            mock.patch.object(hostsys, 'IS_MAC', True), \
                            mock.patch.object(hostsys, 'IS_LINUX', False), \
                            mock.patch.object(hostsys, 'IS_WINDOWS', False), \
                            mock.patch.object(hostsys, '_run', return_value=mock.Mock(returncode=0, stdout='')):
                        return hostsys.neighbour_table()
                finally:
                    stop.set()
                    worker.join(2)
                    self.assertFalse(worker.is_alive())

    def test_empty_child_table_uses_live_reader_and_keeps_mac_identity(self):
        self.assertEqual(self.table_with_peer(ARP.encode()), {'192.0.2.50': '12:34:56:ab:cd:ef'})

    def test_reader_cannot_make_an_invalid_or_multicast_mac_a_device(self):
        self.assertEqual(self.table_with_peer(b'? (192.0.2.50) at ff:ff:ff:ff:ff:ff on en0\n'), {})
        self.assertEqual(self.table_with_peer(b'not an arp table\n'), {})

    def test_an_oversized_reader_reply_is_refused(self):
        self.assertEqual(self.table_with_peer((ARP + 'x' * 1048576).encode()), {})


class TopologyLauncherTests(unittest.TestCase):
    def test_real_child_gets_fresh_tables_and_socket_is_removed(self):
        seen = []

        def read():
            seen.append(True)
            return ARP.encode() if len(seen) == 1 else b''

        code = ("import os,socket; "
                "p=os.environ['AUTOFLASH_MACOS_ARP_SOCKET']; "
                "a=socket.socket(socket.AF_UNIX); a.connect(p); "
                "assert b'12:34:56:ab:cd:ef' in a.recv(4096); a.close(); "
                "b=socket.socket(socket.AF_UNIX); b.connect(p); "
                "assert b.recv(4096)==b''; b.close()")
        folders = []
        temporary_directory = tempfile.TemporaryDirectory

        def directory(*args, **kwargs):
            result = temporary_directory(*args, **kwargs)
            folders.append(Path(result.name))
            self.assertEqual(result.name and (Path(result.name).stat().st_mode & 0o777), 0o700)
            return result

        with mock.patch.object(macos_topology.tempfile, 'TemporaryDirectory', side_effect=directory):
            self.assertEqual(macos_topology.run_child([sys.executable, '-c', code], read), 0)
        self.assertEqual(len(seen), 2)
        self.assertTrue(folders)
        self.assertTrue(all(not path.exists() for path in folders))

    def test_child_exit_status_is_preserved(self):
        self.assertEqual(macos_topology.run_child([sys.executable, '-c', 'raise SystemExit(7)']), 7)

    def test_spawn_failure_does_not_leave_reader_running(self):
        with self.assertRaises(FileNotFoundError):
            macos_topology.run_child(['/no-such-liberator-test-command'])

    def test_reader_failure_is_unavailable_not_an_invented_identity(self):
        with mock.patch.object(macos_topology.subprocess, 'run', side_effect=OSError):
            self.assertEqual(macos_topology.read_table(), b'')
        with mock.patch.object(macos_topology.subprocess, 'run',
                               side_effect=subprocess.TimeoutExpired('arp', 2)):
            self.assertEqual(macos_topology.read_table(), b'')

    def test_launcher_waits_for_child_after_foreground_interrupt(self):
        code = "import os,signal,time; os.kill(os.getppid(),signal.SIGINT); time.sleep(.1); raise SystemExit(7)"
        launcher = Path(macos_topology.__file__)
        result = subprocess.run([sys.executable, str(launcher), sys.executable, '-c', code],
                                capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 7, result.stderr)
