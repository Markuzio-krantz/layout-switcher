"""Install the layout switcher into a fixed local folder and start it with Windows.

  python install.py               install / update, start at login, start now
  python install.py --no-autostart
  python install.py --uninstall   stop, remove the login entry and the folder

Target: %LOCALAPPDATA%\\Programs\\LayoutSwitcher (no admin rights, no git needed).
Autostart is one value under HKCU\\...\\CurrentVersion\\Run, visible in
Task Manager > Startup apps, where it can also be switched off.
An existing config.json in the target is kept, so updates don't reset settings.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import time
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "LayoutSwitcher")
FILES = ["switcher.py", "winio.py", "layouts.py", "convert.py", "selection.py", "start.cmd", "stop.cmd",
         "README-he.md", "install.py"]
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "LayoutSwitcher"
STOP_EVENT = "Local\\LayoutSwitcherStop"


def pythonw() -> str:
    cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return cand if os.path.exists(cand) else sys.executable


def stop_running() -> None:
    k32 = ctypes.windll.kernel32
    h = k32.OpenEventW(0x0002, False, STOP_EVENT)
    if h:
        k32.SetEvent(h)
        k32.CloseHandle(h)
        for _ in range(30):  # wait until it has released the hotkey
            time.sleep(0.1)
            h = k32.OpenEventW(0x0002, False, STOP_EVENT)
            if not h:
                return
            k32.CloseHandle(h)


def set_autostart(on: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            cmd = f'"{pythonw()}" "{os.path.join(TARGET, "switcher.py")}"'
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, cmd)
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


def install(autostart: bool) -> None:
    stop_running()
    os.makedirs(TARGET, exist_ok=True)
    for name in FILES:
        shutil.copy2(os.path.join(HERE, name), os.path.join(TARGET, name))
    cfg = os.path.join(TARGET, "config.json")
    if not os.path.exists(cfg):
        shutil.copy2(os.path.join(HERE, "config.json"), cfg)
    set_autostart(autostart)
    subprocess.Popen([pythonw(), os.path.join(TARGET, "switcher.py")],
                     creationflags=0x00000008)  # DETACHED_PROCESS
    print(f"installed to {TARGET}")
    print("autostart: " + ("on" if autostart else "off"))
    print("running - hotkey is in config.json (default Ctrl+Shift+Space)")


def uninstall() -> None:
    stop_running()
    set_autostart(False)
    if os.path.isdir(TARGET):
        shutil.rmtree(TARGET)
    print("removed")


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install(autostart="--no-autostart" not in sys.argv)
