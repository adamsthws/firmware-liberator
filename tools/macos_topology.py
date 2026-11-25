#!/usr/bin/env python3
"""Run the tool with a live ARP reader outside macOS 27's child restriction.

start.sh runs this with Apple's /usr/bin/python3 before launching the selected
Python. Keep this standalone and compatible with Apple's Python 3.9: the tool
it launches still requires Python 3.10+. No daemon or topology cache is kept.
"""
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import threading


def read_table():
    """Only this fixed read-only command; callers cannot supply a command."""
    try:
        result = subprocess.run(['/usr/sbin/arp', '-an'], capture_output=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return b''
    return result.stdout if result.returncode == 0 and len(result.stdout) <= 1048576 else b''


def run_child(command, reader=None):
    """Own a private socket and reader for exactly the lifetime of one child."""
    if reader is None:
        reader = read_table
    # The usual macOS TMPDIR can exceed AF_UNIX's pathname length limit.
    with tempfile.TemporaryDirectory(prefix='fl-arp-', dir='/tmp') as directory:
        path = str(Path(directory) / 'reader.sock')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(path)
            os.chmod(path, 0o600)
            server.listen(8)
            server.settimeout(0.2)
            stop = threading.Event()

            def serve():
                while not stop.is_set():
                    try:
                        connection, _ = server.accept()
                    except socket.timeout:
                        continue
                    with connection:
                        connection.settimeout(3)
                        try:
                            # A fresh snapshot for each call, never stale cached data.
                            connection.sendall(reader())
                        except (OSError, subprocess.SubprocessError):
                            pass

            worker = threading.Thread(target=serve, name='macos-arp-reader')
            worker.start()
            try:
                env = {**os.environ, 'AUTOFLASH_MACOS_ARP_SOCKET': path}
                return subprocess.call(command, env=env)
            finally:
                stop.set()
                worker.join()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('Usage: macos_topology.py PYTHON SCRIPT [arguments ...]')
    # The foreground child handles Ctrl-C and its recovery instructions. Both
    # receive the terminal signal; keep the reader alive until the child exits.
    signal.signal(signal.SIGINT, lambda *_: None)
    raise SystemExit(run_child(sys.argv[1:]))
