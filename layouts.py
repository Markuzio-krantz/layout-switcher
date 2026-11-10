"""Read the keyboard layouts loaded in Windows and build Layout maps from them.

Nothing is hard-coded: every key's character comes from ToUnicodeEx on the
layouts the user actually has, so a layout change is picked up on the next press.
Run `python layouts.py --dump FILE` to snapshot the maps (used as test fixture).
"""
from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes as w

from convert import Layout

# Typing keys: number row, three letter rows, and the ISO key next to left Shift.
SCANCODES = (
    tuple(range(0x02, 0x0E)) + tuple(range(0x10, 0x1C)) + tuple(range(0x1E, 0x2A))
    + (0x2B,) + tuple(range(0x2C, 0x36)) + (0x56,)
)
LANG_NAMES = {0x09: "en", 0x0D: "he", 0x19: "ru"}  # primary language id -> short name

_u = ctypes.WinDLL("user32", use_last_error=True)
_u.GetKeyboardLayoutList.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_void_p)]
_u.ToUnicodeEx.argtypes = [w.UINT, w.UINT, ctypes.POINTER(ctypes.c_ubyte), w.LPWSTR,
                           ctypes.c_int, w.UINT, ctypes.c_void_p]
_u.MapVirtualKeyExW.argtypes = [w.UINT, w.UINT, ctypes.c_void_p]
_u.MapVirtualKeyExW.restype = w.UINT

VK_SHIFT = 0x10
MAPVK_VSC_TO_VK_EX = 3
TU_NO_STATE_CHANGE = 0x4  # Windows 10 1607+: do not touch the dead-key state


def _signed(h: int) -> int:
    return h - (1 << 64) if h >= 1 << 63 else h


def lang_name(hkl: int) -> str:
    lang = hkl & 0xFFFF
    return LANG_NAMES.get(lang & 0x3FF, f"{lang:04x}")


def _char(vk: int, sc: int, hkl: int, shift: bool) -> str:
    state = (ctypes.c_ubyte * 256)()
    if shift:
        state[VK_SHIFT] = 0x80
    buf = ctypes.create_unicode_buffer(8)
    r = _u.ToUnicodeEx(vk, sc, state, buf, 8, TU_NO_STATE_CHANGE, hkl)
    if r < 0:  # dead key: flush it and ignore
        _u.ToUnicodeEx(vk, sc, state, buf, 8, TU_NO_STATE_CHANGE, hkl)
        return ""
    return buf.value[:r] if r == 1 else ""


def installed_hkls() -> list[int]:
    n = _u.GetKeyboardLayoutList(0, None)
    arr = (ctypes.c_void_p * n)()
    _u.GetKeyboardLayoutList(n, arr)
    return [_signed(a or 0) for a in arr]


def load_layouts() -> list[Layout]:
    layouts = []
    for hkl in installed_hkls():
        keys = {}
        for sc in SCANCODES:
            vk = _u.MapVirtualKeyExW(sc, MAPVK_VSC_TO_VK_EX, hkl)
            for shift in (False, True):
                ch = _char(vk, sc, hkl, shift)
                if ch:
                    keys[(sc, shift)] = ch
        layouts.append(Layout(lang_name(hkl), hkl, keys))
    return layouts


def to_json(layouts: list[Layout]) -> dict:
    return {l.name: {"hkl": l.hkl, "keys": {f"{sc:#04x}{'+' if sh else ''}": ch
                                            for (sc, sh), ch in sorted(l.keys.items())}}
            for l in layouts}


def from_json(data: dict) -> list[Layout]:
    out = []
    for name, d in data.items():
        keys = {(int(k.rstrip("+"), 16), k.endswith("+")): ch for k, ch in d["keys"].items()}
        out.append(Layout(name, d["hkl"], keys))
    return out


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    data = to_json(load_layouts())
    if len(sys.argv) == 3 and sys.argv[1] == "--dump":
        with open(sys.argv[2], "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
    for name, d in data.items():
        print(name, hex(d["hkl"] & 0xFFFFFFFF), "".join(v for k, v in d["keys"].items() if "+" not in k))
