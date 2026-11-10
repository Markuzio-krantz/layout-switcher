"""Shared by the macOS and Linux backends: one instance per user, stop, helper commands."""
from __future__ import annotations

import fcntl
import functools
import logging
import os
import shutil
import signal
import socket
import subprocess
import tempfile

log = logging.getLogger("LayoutSwitcher")


def runtime_dir() -> str:
    """A directory only this user can enter (XDG_RUNTIME_DIR on Linux, per-user TMPDIR on macOS)."""
    base = os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir()
    d = os.path.join(base, f"layout-switcher-{os.getuid()}")
    os.makedirs(d, mode=0o700, exist_ok=True)
    st = os.stat(d)
    if st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise PermissionError(f"{d} is not private to this user")
    return d


_lock_fd: int | None = None


def claim_single_instance() -> bool:
    """True if this is the only running instance (an flock held until the process exits)."""
    global _lock_fd
    path = os.path.join(runtime_dir(), "switcher.pid")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return False
    os.ftruncate(fd, 0)
    os.write(fd, str(os.getpid()).encode())
    _lock_fd = fd
    return True


def running_pid() -> int | None:
    path = os.path.join(runtime_dir(), "switcher.pid")
    try:
        fd = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
            return None  # nobody holds the lock: not running
        except OSError:
            pass
        data = os.read(fd, 32).strip()
        return int(data) if data.isdigit() else None
    finally:
        os.close(fd)


def stop() -> int:
    pid = running_pid()
    if not pid:
        print("not running")
        return 1
    os.kill(pid, signal.SIGTERM)
    print("stopped")
    return 0


@functools.lru_cache(maxsize=64)
def have(tool: str) -> bool:
    """Cached PATH lookup (a miss scans every PATH folder; slow on WSL)."""
    return shutil.which(tool) is not None


def run(cmd: list[str], data: bytes | None = None, timeout: float = 2.0) -> subprocess.CompletedProcess | None:
    """Run a helper program; None if it is missing or hangs. Never goes through a shell."""
    if not have(cmd[0]):
        return None
    try:
        return subprocess.run(cmd, input=data, capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.debug("%s failed: %s", cmd[0], e)
        return None


# ---- `--convert` hands a press to the running instance over a Unix socket ------------
def _socket_path() -> str:
    return os.path.join(runtime_dir(), "switcher.sock")


def _exit_on_signal(*_) -> None:
    raise SystemExit(0)  # unwinds serve_socket(), which removes the socket


def serve_socket(on_hotkey, notify) -> int:
    """Block, running on_hotkey() for every `switcher.py --convert`. The socket sits in
    a directory only this user can open, so only the user's own programs can poke it."""
    if not claim_single_instance():
        notify("Layout Switcher כבר רץ.")
        return 1
    path = _socket_path()
    if os.path.exists(path):
        os.unlink(path)  # left by a crash; we hold the instance lock
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(4)
    signal.signal(signal.SIGTERM, _exit_on_signal)
    try:
        while True:
            conn, _ = srv.accept()
            with conn:
                conn.settimeout(2)
                try:
                    if conn.recv(64).strip() != b"convert":
                        continue
                except OSError:
                    continue
                try:
                    status = on_hotkey()
                except Exception:
                    log.exception("hotkey failed")
                    status = "error"
                try:
                    conn.sendall(str(status).encode() + b"\n")
                except OSError:
                    pass
    finally:
        srv.close()
        if os.path.exists(path):
            os.unlink(path)


def trigger() -> bool:
    """Pass one press to the running instance; False if none is listening."""
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(20)
        c.connect(_socket_path())
    except OSError:
        return False
    with c:
        c.sendall(b"convert\n")
        try:
            c.recv(64)
        except OSError:
            pass
    return True
