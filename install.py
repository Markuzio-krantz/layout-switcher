"""Install the layout switcher into a fixed per-user folder and start it at login.

  python install.py               install / update, start at login, start now
  python install.py --no-autostart
  python install.py --uninstall   stop, remove the login entry and the folder

No admin rights, no git, no packages from PyPI. An existing config.json in the
target is kept, so updates don't reset settings.

  Windows  %LOCALAPPDATA%\\Programs\\LayoutSwitcher; one value under
           HKCU\\...\\CurrentVersion\\Run (Task Manager > Startup apps can switch it off)
  macOS    ~/Library/Application Support/LayoutSwitcher; a LaunchAgent in
           ~/Library/LaunchAgents (System Settings > General > Login Items lists it)
  Linux    ~/.local/share/layout-switcher; ~/.config/autostart/layout-switcher.desktop;
           on GNOME also a custom keyboard shortcut that runs `switcher.py --convert`
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ["switcher.py", "layouts.py", "convert.py", "selection.py", "install.py", "README-he.md",
         "detect.py", "lm.py", "train_lm.py", "corpus.py",
         "backends/__init__.py", "backends/windows.py", "backends/windows_hotkey.py",
         "backends/posix.py", "backends/macos.py", "backends/linux.py"]
WIN = sys.platform == "win32"
MAC = sys.platform == "darwin"
HOME = os.path.expanduser("~")
if WIN:
    TARGET = os.path.join(os.environ["LOCALAPPDATA"], "Programs", "LayoutSwitcher")
    FILES += ["start.cmd", "stop.cmd"]
elif MAC:
    TARGET = os.path.join(HOME, "Library", "Application Support", "LayoutSwitcher")
else:
    TARGET = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.join(HOME, ".local", "share"),
                          "layout-switcher")
SWITCHER = os.path.join(TARGET, "switcher.py")


def python() -> str:
    if WIN:  # pythonw: no console window
        cand = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
        return cand if os.path.exists(cand) else sys.executable
    if MAC:  # the real binary: that is the one the Accessibility permission is given to
        return os.path.realpath(sys.executable)
    return sys.executable  # e.g. /usr/bin/python3, which survives a Python upgrade


def stop_running() -> None:
    if os.path.exists(SWITCHER):
        r = subprocess.run([sys.executable, SWITCHER, "--stop"], capture_output=True, text=True)
        if "stopped" in r.stdout:
            time.sleep(0.5)  # let it release the hotkey / socket


def copy_files() -> None:
    for name in FILES:
        dst = os.path.join(TARGET, name)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(os.path.join(HERE, name), dst)
    cfg = os.path.join(TARGET, "config.json")
    if not os.path.exists(cfg):
        shutil.copy2(os.path.join(HERE, "config.json"), cfg)


def start_now() -> None:
    if WIN:
        flag = os.path.join(os.environ.get("LOCALAPPDATA", HOME), "layout-switcher", "stopped-by-user")
        if os.path.exists(flag):  # stop_running() set it; installing means "run it"
            os.remove(flag)
        # Through Task Scheduler when the watchdog task exists: then the tool is not a child
        # of this installer or of whatever terminal ran it.
        if os.environ.get("LSW_DIRECT_START") != "1" and \
                subprocess.run(["schtasks", "/run", "/tn", TASK], capture_output=True).returncode == 0:
            return
        # no task, or this installer already runs outside any app container (LSW_DIRECT_START)
        subprocess.Popen([python(), SWITCHER, "--supervise", "--from-task"],
                         creationflags=0x00000008)  # DETACHED_PROCESS
    else:
        subprocess.Popen([python(), SWITCHER, "--supervise"], start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---- Windows ------------------------------------------------------------------------------
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "LayoutSwitcher"


TASK = "LayoutSwitcher Watchdog"


def watchdog_windows(on: bool) -> None:
    """A per-user scheduled task that runs `switcher.py --ensure` every 5 minutes: it
    starts the tool if something killed it (not if the user stopped it). Runs from Task
    Scheduler, so it does not depend on whichever program happened to start the tool."""
    subprocess.run(["schtasks", "/delete", "/f", "/tn", TASK], capture_output=True)
    if on:
        r = subprocess.run(["schtasks", "/create", "/f", "/tn", TASK, "/sc", "minute", "/mo", "5",
                            "/tr", f'"{python()}" "{SWITCHER}" --ensure'],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("watchdog task not created:", (r.stderr or r.stdout).strip())


def autostart_windows(on: bool) -> None:
    import winreg
    watchdog_windows(on)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, f'"{python()}" "{SWITCHER}" --supervise')
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError:
                pass


# ---- macOS ----------------------------------------------------------------------------------
LABEL = "io.github.markuzio-krantz.layout-switcher"
PLIST = os.path.join(HOME, "Library", "LaunchAgents", f"{LABEL}.plist")


def autostart_macos(on: bool) -> None:
    import plistlib
    domain = f"gui/{os.getuid()}"
    subprocess.run(["launchctl", "bootout", f"{domain}/{LABEL}"], capture_output=True)
    if not on:
        if os.path.exists(PLIST):
            os.remove(PLIST)
        return
    os.makedirs(os.path.dirname(PLIST), exist_ok=True)
    with open(PLIST, "wb") as f:
        plistlib.dump({"Label": LABEL, "ProgramArguments": [python(), SWITCHER, "--supervise"],
                       "RunAtLoad": True, "ProcessType": "Interactive"}, f)
    subprocess.run(["launchctl", "bootstrap", domain, PLIST], capture_output=True)


# ---- Linux ------------------------------------------------------------------------------------
DESKTOP = os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(HOME, ".config"),
                       "autostart", "layout-switcher.desktop")
GNOME_KEYS = "org.gnome.settings-daemon.plugins.media-keys"
GNOME_PATH = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/layout-switcher/"


def autostart_linux(on: bool) -> None:
    if not on:
        if os.path.exists(DESKTOP):
            os.remove(DESKTOP)
        return
    os.makedirs(os.path.dirname(DESKTOP), exist_ok=True)
    with open(DESKTOP, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Application\nName=Layout Switcher\n"
                "Comment=Converts text typed in the wrong keyboard layout\n"
                f'Exec="{python()}" "{SWITCHER}" --supervise\nTerminal=false\nNoDisplay=true\n'
                "X-GNOME-Autostart-enabled=true\n")


def gnome_binding(spec: str) -> str:
    """'ctrl+shift+space' -> '<Control><Shift>space'."""
    mods = {"ctrl": "<Control>", "control": "<Control>", "shift": "<Shift>", "alt": "<Alt>",
            "win": "<Super>", "super": "<Super>"}
    keys = {"space": "space", "pause": "Pause", "insert": "Insert", "backquote": "grave",
            "tab": "Tab", "scrolllock": "Scroll_Lock"}
    out = ""
    for part in spec.lower().replace(" ", "").split("+"):
        if part in mods:
            out += mods[part]
        else:
            out += keys.get(part, part.upper() if re.fullmatch(r"f\d+", part) else part)
    return out


def _gvariant(s: str) -> str:
    return "'" + s.replace("\\", "\\\\").replace("'", "\\'") + "'"


def shortcut_gnome(on: bool, hotkey: str) -> bool:
    """Add (or remove) a GNOME custom shortcut. False if this is not GNOME."""
    if not shutil.which("gsettings"):
        return False
    r = subprocess.run(["gsettings", "get", GNOME_KEYS, "custom-keybindings"], capture_output=True, text=True)
    if r.returncode != 0:
        return False
    paths = [p for p in re.findall(r"'([^']+)'", r.stdout) if p != GNOME_PATH]
    if on:
        paths.append(GNOME_PATH)
        schema = f"{GNOME_KEYS}.custom-keybinding:{GNOME_PATH}"
        command = f'"{python()}" "{SWITCHER}" --convert'
        for key, value in (("name", "Layout Switcher"), ("command", command),
                           ("binding", gnome_binding(hotkey))):
            subprocess.run(["gsettings", "set", schema, key, _gvariant(value)], check=True)
    subprocess.run(["gsettings", "set", GNOME_KEYS, "custom-keybindings",
                    "[" + ", ".join(_gvariant(p) for p in paths) + "]"], check=True)
    return True


def linux_missing() -> list[str]:
    import ctypes.util
    wayland = os.environ.get("XDG_SESSION_TYPE") == "wayland" or (
        os.environ.get("WAYLAND_DISPLAY") and os.environ.get("XDG_SESSION_TYPE") != "x11")
    need = []
    if not ctypes.util.find_library("xkbcommon"):
        need.append("libxkbcommon")
    if wayland:
        if not (shutil.which("wl-copy") and shutil.which("wl-paste")):
            need.append("wl-clipboard")
        if not (shutil.which("wtype") or shutil.which("ydotool")):
            need.append("wtype (sway/Hyprland) or ydotool (GNOME/KDE)")
    else:
        if not shutil.which("xdotool"):
            need.append("xdotool")
        if not (shutil.which("xclip") or shutil.which("xsel")):
            need.append("xclip")
    return need


def linux_report(hotkey: str) -> None:
    missing = linux_missing()
    if missing:
        print("missing tools: " + ", ".join(missing))
        print("  Ubuntu/Debian: sudo apt install <name>   Fedora: sudo dnf install <name>"
              "   Arch: sudo pacman -S <name>")
    if shortcut_gnome(True, hotkey):
        print(f"GNOME shortcut {gnome_binding(hotkey)} -> switcher.py --convert")
    else:
        print("bind a keyboard shortcut in your desktop settings to run:")
        print(f'  "{python()}" "{SWITCHER}" --convert')


# ---- main -----------------------------------------------------------------------------------
def set_autostart(on: bool) -> None:
    (autostart_windows if WIN else autostart_macos if MAC else autostart_linux)(on)


def hotkey() -> str:
    import json
    try:
        with open(os.path.join(TARGET, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("hotkey", "ctrl+shift+space")
    except (OSError, ValueError):
        return "ctrl+shift+space"


def install(autostart: bool) -> None:
    stop_running()
    copy_files()
    set_autostart(autostart)
    if not (MAC and autostart):  # the LaunchAgent has already started it
        start_now()
    print(f"installed to {TARGET}")
    print("autostart: " + ("on" if autostart else "off"))
    if MAC:
        print("allow Python in System Settings > Privacy & Security > Accessibility,")
        print("then the hotkey from config.json works (default Ctrl+Shift+Space)")
    elif not WIN:
        linux_report(hotkey())
    else:
        print("running - hotkey is in config.json (default Ctrl+Shift+Space)")


def uninstall() -> None:
    stop_running()
    set_autostart(False)
    if not WIN and not MAC:
        shortcut_gnome(False, "")
    if os.path.isdir(TARGET):
        shutil.rmtree(TARGET)
    print("removed")


if __name__ == "__main__":
    if "--uninstall" in sys.argv:
        uninstall()
    else:
        install(autostart="--no-autostart" not in sys.argv)
