"""Windows hotkey loop: RegisterHotKey, one instance per user session, stop event."""
from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes as w
from typing import Callable

APP = "LayoutSwitcher"
log = logging.getLogger(APP)

MODS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
NAMED_VK = {"space": 0x20, "pause": 0x13, "insert": 0x2D, "scrolllock": 0x91,
            "capslock": 0x14, "tab": 0x09, "backquote": 0xC0}
MOD_NOREPEAT = 0x4000


def parse_hotkey(spec: str) -> tuple[int, int]:
    mods, vk = 0, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in MODS:
            mods |= MODS[part]
        elif part in NAMED_VK:
            vk = NAMED_VK[part]
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part[:1] == "f" and part[1:].isdigit() and 1 <= int(part[1:]) <= 24:
            vk = 0x6F + int(part[1:])
        else:
            raise ValueError(f"unknown key in hotkey: {part!r}")
    if vk is None:
        raise ValueError("hotkey needs a non-modifier key")
    return mods, vk


k32 = ctypes.WinDLL("kernel32", use_last_error=True)
u32 = ctypes.WinDLL("user32", use_last_error=True)
k32.CreateMutexW.argtypes = [ctypes.c_void_p, w.BOOL, w.LPCWSTR]
k32.CreateMutexW.restype = w.HANDLE
k32.CreateEventW.argtypes = [ctypes.c_void_p, w.BOOL, w.BOOL, w.LPCWSTR]
k32.CreateEventW.restype = w.HANDLE
k32.OpenEventW.argtypes = [w.DWORD, w.BOOL, w.LPCWSTR]
k32.OpenEventW.restype = w.HANDLE
k32.SetEvent.argtypes = [w.HANDLE]
u32.RegisterHotKey.argtypes = [w.HWND, ctypes.c_int, w.UINT, w.UINT]
u32.MsgWaitForMultipleObjects.argtypes = [w.DWORD, ctypes.POINTER(w.HANDLE), w.BOOL, w.DWORD, w.DWORD]
u32.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
u32.MessageBoxW.argtypes = [w.HWND, w.LPCWSTR, w.LPCWSTR, w.UINT]

STOP_EVENT = f"Local\\{APP}Stop"
WM_HOTKEY, PM_REMOVE, QS_ALLINPUT, INFINITE = 0x0312, 1, 0x04FF, 0xFFFFFFFF
ERROR_ALREADY_EXISTS = 183


def notify(text: str) -> None:
    u32.MessageBoxW(None, text, "Layout Switcher", 0x40)


def stop() -> int:
    h = k32.OpenEventW(0x0002, False, STOP_EVENT)  # EVENT_MODIFY_STATE
    if not h:
        print("not running")
        return 1
    k32.SetEvent(h)
    print("stopped")
    return 0


def serve(cfg: dict, on_hotkey: Callable[[], object]) -> int:
    """Block until stop(): call on_hotkey() for every press of cfg['hotkey']."""
    k32.CreateMutexW(None, False, f"Local\\{APP}")
    if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
        notify("Layout Switcher כבר רץ.")
        return 1
    try:
        mods, vk = parse_hotkey(cfg["hotkey"])
    except ValueError as e:
        log.error("bad hotkey in config.json: %s", e)
        notify(f"שגיאה ב-config.json: {e}")
        return 1
    if not u32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk):
        log.error("hotkey %s is taken by another program", cfg["hotkey"])
        notify(f"הקיצור {cfg['hotkey']} תפוס על ידי תוכנה אחרת. שנה אותו ב-config.json.")
        return 1
    stop_ev = k32.CreateEventW(None, True, False, STOP_EVENT)
    msg = w.MSG()
    handles = (w.HANDLE * 1)(stop_ev)
    while True:
        r = u32.MsgWaitForMultipleObjects(1, handles, False, INFINITE, QS_ALLINPUT)
        if r == 0:
            break
        while u32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            if msg.message == WM_HOTKEY:
                try:
                    on_hotkey()
                except Exception:
                    log.exception("hotkey failed")
    return 0
