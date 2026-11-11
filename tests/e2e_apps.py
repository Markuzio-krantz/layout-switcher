"""End-to-end check in real apps. Takes over keyboard focus for ~1-2 minutes.

  python tests/e2e_apps.py notepad chrome vscode word

Starts its own switcher instance (stop any running one first), types text into a
scratch document in each app, presses the real hotkey, and reads the result back.
All six directions en/he/ru are covered, plus "clipboard restored after each press".
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import tempfile
import time
from ctypes import wintypes as w

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from backends import windows as io  # noqa: E402
from backends.windows_hotkey import STOP_EVENT, parse_hotkey  # noqa: E402
from switcher import load_config  # noqa: E402

u32 = ctypes.WinDLL("user32", use_last_error=True)
u32.GetWindowTextW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
u32.IsWindowVisible.argtypes = [w.HWND]
u32.SetForegroundWindow.argtypes = [w.HWND]
u32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.c_void_p]
u32.AttachThreadInput.argtypes = [w.DWORD, w.DWORD, w.BOOL]
u32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
u32.BringWindowToTop.argtypes = [w.HWND]
u32.SwitchToThisWindow.argtypes = [w.HWND, w.BOOL]
k32 = ctypes.WinDLL("kernel32")

TMP = os.path.join(tempfile.gettempdir(), "lsw_e2e")
RUN = time.strftime("%H%M%S")  # fresh file names: Notepad restores unsaved tabs
os.makedirs(TMP, exist_ok=True)
VK_A, VK_C, VK_DELETE = ord("A"), ord("C"), 0x2E

# (what to type, select it first?, [expected after press 1, press 2, ...])
CASES = [
    ("Akuo xu", True, ["שלום סו"]),  # Shift in en must still give Hebrew letters                                    # en -> he (selection)
    ("אני כותב akuo", False, ["אני כותב שלום", "אני כותב флгщ",         # en -> he -> ru -> back
                              "אני כותב akuo"]),
    ("יקךךם", True, ["hello"]),                                        # he -> en
    ("Hi שלום", False, ["Hi akuo", "Hi флгщ"]),   # he -> en -> ru (capital: Word autocapitalises)
    ("Руддщ црщ", False, ["Hello who", "יקךךם 'ים"]),   # ru -> en (model's guess first) -> he
    # stage 2: real words around the wrong-layout ones stay as they are
    ("hello akuo", False, ["hello שלום", "hello флгщ", "hello akuo"]),
    ("I think ghbdtn world", False, ["I think привет world", "I think עינגאמ world"]),
    ("ghbdtn", True, ["привет"]),                                      # selection: guess first
]


def find_window(fragment: str, timeout: float = 25) -> int:
    found = []

    @ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
    def cb(hwnd, _):
        if u32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(512)
            u32.GetWindowTextW(hwnd, buf, 512)
            if fragment.lower() in buf.value.lower():
                found.append(hwnd)
        return True

    end = time.time() + timeout
    while time.time() < end:
        found.clear()
        u32.EnumWindows(cb, 0)
        if found:
            return found[0]
        time.sleep(0.3)
    raise RuntimeError(f"window not found: {fragment}")


def focus(hwnd: int) -> None:
    for attempt in range(6):
        if u32.GetForegroundWindow() == hwnd:
            return
        fg = u32.GetForegroundWindow()
        me = k32.GetCurrentThreadId()
        other = u32.GetWindowThreadProcessId(fg, None)
        u32.AttachThreadInput(me, other, True)
        u32.ShowWindow(hwnd, 9)  # SW_RESTORE
        u32.BringWindowToTop(hwnd)
        u32.SetForegroundWindow(hwnd)
        u32.AttachThreadInput(me, other, False)
        if attempt >= 2:
            u32.SwitchToThisWindow(hwnd, True)
        time.sleep(0.4)
    if u32.GetForegroundWindow() != hwnd:
        raise RuntimeError("could not focus window")


LOG = os.path.join(os.environ.get("LOCALAPPDATA", ""), "layout-switcher", "switcher.log")


def done_count() -> int:
    """Lines the switcher writes when a press is fully handled (clipboard restored)."""
    try:
        with open(LOG, encoding="utf-8") as f:
            return sum(1 for line in f if " -> " in line)
    except OSError:
        return 0


def press_and_wait(timeout: float = 15) -> None:
    before = done_count()
    press_hotkey()
    end = time.time() + timeout
    while done_count() == before and time.time() < end:
        time.sleep(0.1)
    time.sleep(1.0)  # the app may insert the paste a moment after the log line (slow under load)


def press_hotkey() -> None:
    mods, vk = parse_hotkey(load_config()["hotkey"])
    keys = [v for bit, v in ((0x2, io.VK_CONTROL), (0x1, io.VK_MENU), (0x4, io.VK_SHIFT),
                             (0x8, io.VK_LWIN)) if mods & bit] + [vk]
    io.send_combo(*keys)


def read_all() -> str:
    io.send_combo(io.VK_CONTROL, VK_A)
    time.sleep(0.15)
    text, _ = io.copy_and_read(1.0)
    io.send_combo(io.VK_CONTROL, VK_A)  # keep state predictable
    return (text or "").rstrip("\r\n")


def clear() -> None:
    io.send_combo(io.VK_CONTROL, VK_A)
    io.send_combo(VK_DELETE)
    time.sleep(0.2)


def run_cases(app: str, hwnd: int) -> list[str]:
    fails = []
    for typed, select, expected in CASES:
        focus(hwnd)
        clear()
        io.send_unicode(typed, delay=0.04)
        time.sleep(0.6)
        before = read_all()
        io.send_combo(io.VK_CONTROL, 0x23)  # Ctrl+End: caret back to the end
        time.sleep(0.2)
        if before != typed:
            print(f"  [SKIP] {app}: typing came out as {before!r}, not {typed!r}")
            fails.append(f"{app}: typing {typed!r} came out as {before!r}")
            continue
        if select:
            io.send_combo(io.VK_CONTROL, VK_A)
            time.sleep(0.2)
        for i, want in enumerate(expected):
            sentinel = f"SENTINEL {app} {typed} {i}"
            io.set_text(sentinel)
            press_and_wait()
            clip, _ = io.get_text()
            focus(hwnd)
            got = read_all()
            # put the caret back at the end, unselected, for the next press
            io.send_combo(io.VK_CONTROL, 0x23)      # Ctrl+End
            time.sleep(0.2)
            ok = got == want and clip == sentinel
            print(f"  [{'OK ' if ok else 'FAIL'}] {app}: {typed!r} press {i + 1} -> {got!r}"
                  + ("" if clip == sentinel else f"  (clipboard NOT restored: {clip!r})"))
            if not ok:
                fails.append(f"{app}: {typed!r} press {i + 1}: want {want!r} got {got!r}")
    return fails


def open_app(app: str):
    """Returns (hwnd, cleanup callable)."""
    if app == "notepad":
        path = os.path.join(TMP, f"lsw_notepad_{RUN}.txt")
        open(path, "w", encoding="utf-8").close()
        p = subprocess.Popen(["notepad.exe", path])
        hwnd = find_window(f"lsw_notepad_{RUN}")
        return hwnd, lambda: (focus(hwnd), clear(), io.send_combo(io.VK_CONTROL, ord("S")),
                              time.sleep(0.3), io.send_combo(io.VK_CONTROL, ord("W")))
    if app == "chrome":
        path = os.path.join(TMP, f"lsw_chrome_{RUN}.html")
        with open(path, "w", encoding="utf-8") as f:
            f.write("<!doctype html><meta charset=utf-8><title>lsw_chrome_" + RUN + "</title>"
                    "<textarea autofocus style='width:90vw;height:60vh'></textarea>")
        exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        subprocess.Popen([exe, "--new-window", "file:///" + path.replace("\\", "/")])
        hwnd = find_window(f"lsw_chrome_{RUN}")
        return hwnd, lambda: (focus(hwnd), io.send_combo(io.VK_CONTROL, ord("W")))
    if app == "vscode":
        path = os.path.join(TMP, f"lsw_vscode_{RUN}.txt")
        open(path, "w", encoding="utf-8").close()
        subprocess.Popen(["cmd", "/c", "code", "--new-window", path])
        hwnd = find_window(f"lsw_vscode_{RUN}")
        time.sleep(2.5)  # editor needs a moment to take focus
        return hwnd, lambda: (focus(hwnd), clear(), io.send_combo(io.VK_CONTROL, ord("S")),
                              time.sleep(0.3), io.send_combo(io.VK_CONTROL, ord("W")))
    if app == "word":
        import win32com.client
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = True
        doc = word.Documents.Add()
        time.sleep(4)  # a freshly started Word is slow to accept input
        hwnd = word.ActiveWindow.Hwnd
        return hwnd, lambda: (doc.Close(0), word.Quit())
    raise ValueError(app)


def main(apps: list[str]) -> int:
    if k32.OpenEventW(0x0002, False, STOP_EVENT):
        print("a switcher is already running; stop it first (switcher.py --stop)")
        return 2
    sw = subprocess.Popen([sys.executable, os.path.join(ROOT, "switcher.py")])
    for _ in range(30):  # the stop event exists only once the hotkey is registered
        time.sleep(0.1)
        if k32.OpenEventW(0x0002, False, STOP_EVENT):
            break
    else:
        sw.kill()
        print("switcher did not start (hotkey taken? see the log)")
        return 2
    all_fails = []
    try:
        for app in apps:
            print(f"== {app}")
            hwnd, cleanup = open_app(app)
            try:
                time.sleep(1.0)
                all_fails += run_cases(app, hwnd)
            finally:
                try:
                    cleanup()
                except Exception as e:  # noqa: BLE001
                    print("  cleanup:", e)
    finally:
        subprocess.run([sys.executable, os.path.join(ROOT, "switcher.py"), "--stop"])
        sw.wait(5)
    print("\nRESULT:", "ALL PASSED" if not all_fails else f"{len(all_fails)} failed")
    for f in all_fails:
        print("  -", f)
    return 1 if all_fails else 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main(sys.argv[1:] or ["notepad"]))
