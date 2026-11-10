"""macOS backend, standard library only (ctypes on the system frameworks):

  layouts    Text Input Sources + UCKeyTranslate on the input sources enabled in
             System Settings > Keyboard > Input Sources
  clipboard  NSPasteboard: every item and type is saved and put back
  keys       Quartz CGEventPost (needs the Accessibility permission for Python)
  hotkey     Carbon RegisterEventHotKey, the macOS counterpart of RegisterHotKey:
             the system tells us about that one combination only; no keyboard is read.

Selecting to line start is Cmd+Shift+Left, copy Cmd+C, paste Cmd+V.
"""
from __future__ import annotations

import ctypes
import logging
import os
import signal
import time
from ctypes import POINTER, byref, c_bool, c_char_p, c_int32, c_long, c_uint8, c_uint16, c_uint32, \
    c_uint64, c_ulong, c_void_p

from convert import Layout

from . import posix
from .posix import stop  # noqa: F401  (backend interface)

NAME = "macos"
log = logging.getLogger("LayoutSwitcher")
LOG_DIR = os.path.expanduser("~/Library/Logs/layout-switcher")
CONSOLES = {"com.apple.terminal", "com.googlecode.iterm2", "dev.warp.warp-stable", "io.alacritty", "org.alacritty",
            "net.kovidgoyal.kitty", "com.github.wez.wezterm", "co.zeit.hyper", "com.mitchellh.ghostty"}

FW = "/System/Library/Frameworks/{0}.framework/{0}"
objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
appkit = ctypes.CDLL(FW.format("AppKit"))
carbon = ctypes.CDLL(FW.format("Carbon"))
appserv = ctypes.CDLL(FW.format("ApplicationServices"))
cf = ctypes.CDLL(FW.format("CoreFoundation"))

objc.objc_getClass.restype = c_void_p
objc.objc_getClass.argtypes = [c_char_p]
objc.sel_registerName.restype = c_void_p
objc.sel_registerName.argtypes = [c_char_p]
objc.objc_autoreleasePoolPush.restype = c_void_p
objc.objc_autoreleasePoolPop.argtypes = [c_void_p]
cf.CFRelease.argtypes = [c_void_p]
cf.CFDataGetBytePtr.restype = c_void_p
cf.CFDataGetBytePtr.argtypes = [c_void_p]
_MSGSEND = ctypes.cast(objc.objc_msgSend, c_void_p).value
_protos: dict = {}


def msg(obj, sel: str, *args, restype=c_void_p, argtypes=()):
    """objc_msgSend with an exact prototype per call shape (required on Apple silicon)."""
    key = (restype, tuple(argtypes))
    if key not in _protos:
        _protos[key] = ctypes.CFUNCTYPE(restype, c_void_p, c_void_p, *argtypes)(_MSGSEND)
    return _protos[key](obj, objc.sel_registerName(sel.encode()), *args)


def cls(name: str) -> int:
    return objc.objc_getClass(name.encode())


def nsstr(s: str) -> int:
    return msg(cls("NSString"), "stringWithUTF8String:", s.encode(), argtypes=(c_char_p,))


def pystr(ns) -> str | None:
    if not ns:
        return None
    raw = msg(ns, "UTF8String", restype=c_char_p)
    return raw.decode("utf-8", "replace") if raw is not None else None


def _const(lib, name: str) -> int:
    return c_void_p.in_dll(lib, name).value


# ---- layouts: Text Input Sources + UCKeyTranslate --------------------------------------
# Virtual key codes of the typing keys (ANSI/ISO): letters, digits, punctuation, § and `.
KEYCODES = tuple(range(0, 36)) + tuple(range(37, 48)) + (50,)
carbon.TISCreateInputSourceList.restype = c_void_p
carbon.TISCreateInputSourceList.argtypes = [c_void_p, c_bool]
carbon.TISGetInputSourceProperty.restype = c_void_p
carbon.TISGetInputSourceProperty.argtypes = [c_void_p, c_void_p]
carbon.TISSelectInputSource.argtypes = [c_void_p]
carbon.LMGetKbdType.restype = c_uint8
carbon.UCKeyTranslate.restype = c_int32
carbon.UCKeyTranslate.argtypes = [c_void_p, c_uint16, c_uint16, c_uint32, c_uint32, c_uint32,
                                  POINTER(c_uint32), c_ulong, POINTER(c_ulong), POINTER(c_uint16)]
kUCKeyActionDown, kUCKeyTranslateNoDeadKeysMask, SHIFT_STATE = 0, 1, 0x02  # (shiftKey >> 8)


def _sources(**match: str) -> int:
    """Enabled keyboard layouts (or the one with a given ID) as a +1 CFArray."""
    d = msg(cls("NSMutableDictionary"), "dictionary")
    if "source_id" in match:
        pairs = [(nsstr(match["source_id"]), "kTISPropertyInputSourceID")]
    else:  # selectable only: an input method's hidden base layout is not one of the user's
        pairs = [(_const(carbon, "kTISTypeKeyboardLayout"), "kTISPropertyInputSourceType"),
                 (_const(cf, "kCFBooleanTrue"), "kTISPropertyInputSourceIsSelectCapable")]
    for value, key in pairs:
        msg(d, "setObject:forKey:", value, _const(carbon, key), restype=None, argtypes=(c_void_p, c_void_p))
    props = d
    return carbon.TISCreateInputSourceList(props, False)


def _items(nsarray) -> list[int]:
    n = msg(nsarray, "count", restype=c_ulong) if nsarray else 0
    return [msg(nsarray, "objectAtIndex:", i, argtypes=(c_ulong,)) for i in range(n)]


def _keys_of(src) -> dict[tuple[int, bool], str]:
    data = carbon.TISGetInputSourceProperty(src, _const(carbon, "kTISPropertyUnicodeKeyLayoutData"))
    if not data:
        return {}
    layout = cf.CFDataGetBytePtr(data)
    kbd = carbon.LMGetKbdType()
    keys = {}
    for code in KEYCODES:
        for shift in (False, True):
            dead, n = c_uint32(0), c_ulong(0)
            buf = (c_uint16 * 4)()
            st = carbon.UCKeyTranslate(layout, code, kUCKeyActionDown, SHIFT_STATE if shift else 0, kbd,
                                       kUCKeyTranslateNoDeadKeysMask, byref(dead), 4, byref(n), buf)
            if st == 0 and n.value == 1 and not dead.value:
                ch = chr(buf[0])
                if ch.isprintable() and not ch.isspace():
                    keys[(code, shift)] = ch
    return keys


def load_layouts() -> list[Layout]:
    """Layout.hkl is the input source ID, e.g. 'com.apple.keylayout.Hebrew'."""
    pool = objc.objc_autoreleasePoolPush()
    arr = _sources()
    try:
        out, seen = [], set()
        for src in _items(arr):
            sid = pystr(carbon.TISGetInputSourceProperty(src, _const(carbon, "kTISPropertyInputSourceID")))
            langs = _items(carbon.TISGetInputSourceProperty(src, _const(carbon, "kTISPropertyInputSourceLanguages")))
            name = (pystr(langs[0]) or "") if langs else ""
            name = name.split("-")[0] or (sid or "?").rsplit(".", 1)[-1].lower()
            if name in seen:
                name = (sid or name).rsplit(".", 1)[-1].lower()
            keys = _keys_of(src)
            if keys:
                seen.add(name)
                out.append(Layout(name, sid, keys))
        return out
    finally:
        if arr:
            cf.CFRelease(arr)
        objc.objc_autoreleasePoolPop(pool)


def switch_input_language(source_id: str) -> None:
    pool = objc.objc_autoreleasePoolPush()
    arr = _sources(source_id=source_id)
    try:
        for src in _items(arr)[:1]:
            carbon.TISSelectInputSource(src)
    finally:
        if arr:
            cf.CFRelease(arr)
        objc.objc_autoreleasePoolPop(pool)


# ---- keys -------------------------------------------------------------------------------
appserv.CGEventCreateKeyboardEvent.restype = c_void_p
appserv.CGEventCreateKeyboardEvent.argtypes = [c_void_p, c_uint16, c_bool]
appserv.CGEventSetFlags.argtypes = [c_void_p, c_uint64]
appserv.CGEventPost.argtypes = [c_uint32, c_void_p]
appserv.CGEventSourceFlagsState.restype = c_uint64
appserv.CGEventSourceFlagsState.argtypes = [c_int32]
appserv.AXIsProcessTrusted.restype = c_bool
appserv.AXIsProcessTrustedWithOptions.restype = c_bool
appserv.AXIsProcessTrustedWithOptions.argtypes = [c_void_p]
appserv.CGEventGetFlags.restype = c_uint64
appserv.CGEventGetFlags.argtypes = [c_void_p]
SHIFT, CTRL, ALT, CMD = 0x20000, 0x40000, 0x80000, 0x100000  # CGEventFlags
KEEP_FLAGS = 0x200000 | 0x800000  # NumericPad / Fn, set by the system on arrow keys
KC_C, KC_V, KC_LEFT, KC_DOWN, KC_UP = 8, 9, 123, 125, 126


def send_combo(flags: int, keycode: int) -> None:
    for down in (True, False):
        ev = appserv.CGEventCreateKeyboardEvent(None, keycode, down)
        appserv.CGEventSetFlags(ev, appserv.CGEventGetFlags(ev) & KEEP_FLAGS | flags)
        appserv.CGEventPost(0, ev)  # kCGHIDEventTap
        cf.CFRelease(ev)
        time.sleep(0.004)


def accessibility_ok(prompt: bool = False) -> bool:
    """Without this permission macOS silently drops the keys we send."""
    if not prompt:
        return appserv.AXIsProcessTrusted()
    pool = objc.objc_autoreleasePoolPush()
    try:
        opts = msg(cls("NSDictionary"), "dictionaryWithObject:forKey:", _const(cf, "kCFBooleanTrue"),
                   _const(appserv, "kAXTrustedCheckOptionPrompt"), argtypes=(c_void_p, c_void_p))
        return appserv.AXIsProcessTrustedWithOptions(opts)
    finally:
        objc.objc_autoreleasePoolPop(pool)


def mask_modifiers() -> None:
    """Nothing to mask on macOS; check the permission here, first thing on every press."""
    if not accessibility_ok():
        log.warning("no Accessibility permission: System Settings > Privacy & Security > Accessibility")


def wait_modifiers_released(timeout: float = 1.5) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not appserv.CGEventSourceFlagsState(1) & (SHIFT | CTRL | ALT | CMD):  # HID state
            return
        time.sleep(0.01)
    # each key we send carries its own flags, so a still-held key does not leak in


def select_to_line_start() -> None:
    send_combo(CMD | SHIFT, KC_LEFT)


class LineOps:
    def copy(self) -> str | None:
        return copy_and_read()[0]

    def line_up(self) -> None:
        send_combo(SHIFT, KC_UP)
        send_combo(CMD | SHIFT, KC_LEFT)

    def line_down(self) -> None:
        send_combo(SHIFT, KC_DOWN)
        send_combo(CMD | SHIFT, KC_LEFT)


# ---- clipboard: NSPasteboard ----------------------------------------------------------
TEXT = "public.utf8-plain-text"
TRANSIENT = "org.nspasteboard.TransientType"  # clipboard managers skip items marked with it


def _pb() -> int:
    return msg(cls("NSPasteboard"), "generalPasteboard")


def _change_count() -> int:
    return msg(_pb(), "changeCount", restype=c_long)


def save_clipboard() -> list[list[tuple[str, bytes]]]:
    pool = objc.objc_autoreleasePoolPush()
    try:
        saved = []
        for item in _items(msg(_pb(), "pasteboardItems")):
            entry = []
            for t in _items(msg(item, "types")):
                data = msg(item, "dataForType:", t, argtypes=(c_void_p,))
                if data:
                    n = msg(data, "length", restype=c_ulong)
                    entry.append((pystr(t), ctypes.string_at(msg(data, "bytes"), n)))
            saved.append(entry)
        return saved
    finally:
        objc.objc_autoreleasePoolPop(pool)


def _write(items: list[list[tuple[str, bytes]]]) -> None:
    pool = objc.objc_autoreleasePoolPush()
    try:
        pb = _pb()
        msg(pb, "clearContents", restype=c_long)
        if not items:
            return
        arr = msg(cls("NSMutableArray"), "array")
        for n, entry in enumerate(items):
            it = msg(msg(cls("NSPasteboardItem"), "alloc"), "init")
            for t, data in entry + ([(TRANSIENT, b"")] if n == 0 else []):
                nsdata = msg(cls("NSData"), "dataWithBytes:length:", data, len(data),
                             argtypes=(c_char_p, c_ulong))
                msg(it, "setData:forType:", nsdata, nsstr(t), restype=c_bool, argtypes=(c_void_p, c_void_p))
            msg(arr, "addObject:", it, restype=None, argtypes=(c_void_p,))
            msg(it, "release", restype=None)
        msg(pb, "writeObjects:", arr, restype=c_bool, argtypes=(c_void_p,))
    finally:
        objc.objc_autoreleasePoolPop(pool)


def restore_clipboard(saved: list[list[tuple[str, bytes]]]) -> None:
    _write(saved)  # marked transient: the original is already in any clipboard history


def set_text(text: str) -> None:
    _write([[(TEXT, text.encode("utf-8"))]])


def get_text() -> str | None:
    pool = objc.objc_autoreleasePoolPush()
    try:
        return pystr(msg(_pb(), "stringForType:", nsstr(TEXT), argtypes=(c_void_p,)))
    finally:
        objc.objc_autoreleasePoolPop(pool)


def copy_and_read(timeout: float = 0.5) -> tuple[str | None, bool]:
    seq = _change_count()
    send_combo(CMD, KC_C)
    end = time.monotonic() + timeout
    while _change_count() == seq:
        if time.monotonic() > end:
            return None, False
        time.sleep(0.01)
    while True:  # some apps clear the pasteboard first and write a moment later
        text = get_text()
        if text is not None or time.monotonic() > end + 0.3:
            return text, False
        time.sleep(0.01)


class Paster:
    def __init__(self) -> None:
        self.sent_at = 0.0
        self.requesters: list[tuple[float, str]] = []

    def paste(self, text: str, settle: float = 0.3) -> bool:
        set_text(text)
        self.sent_at = time.monotonic()
        send_combo(CMD, KC_V)
        time.sleep(settle)  # the app reads the pasteboard on its own time; then we restore
        return True


# ---- focused app, feedback ------------------------------------------------------------------
appkit.NSBeep.restype = None


def foreground_process() -> str:
    """Bundle ID of the frontmost app, lower-case, e.g. 'com.microsoft.vscode'."""
    pool = objc.objc_autoreleasePoolPush()
    try:
        app = msg(msg(cls("NSWorkspace"), "sharedWorkspace"), "frontmostApplication")
        return (pystr(msg(app, "bundleIdentifier")) or "").lower() if app else ""
    finally:
        objc.objc_autoreleasePoolPop(pool)


def beep() -> None:
    appkit.NSBeep()


def notify(text: str) -> None:
    posix.run(["osascript", "-e", "on run argv", "-e",
               'display notification (item 1 of argv) with title "Layout Switcher"',
               "-e", "end run", text])


def configure(cfg: dict) -> None:
    pass


def trigger() -> bool:
    return False  # `--convert` runs in its own process on macOS (the hotkey is the normal way)


# ---- the hotkey: Carbon RegisterEventHotKey under a Dock-less NSApplication -----------------
CARBON_MODS = {"cmd": 0x100, "command": 0x100, "win": 0x100, "shift": 0x200,
               "alt": 0x800, "option": 0x800, "ctrl": 0x1000, "control": 0x1000}
LETTERS = "asdfhgzxcv\0bqweryt"  # key codes 0-17 (10 is the ISO § key)
NAMED = {"space": 49, "tab": 48, "backquote": 50, "o": 31, "u": 32, "i": 34, "p": 35, "l": 37,
         "j": 38, "k": 40, "n": 45, "m": 46, "1": 18, "2": 19, "3": 20, "4": 21, "6": 22, "5": 23,
         "9": 25, "7": 26, "8": 28, "0": 29, "f1": 122, "f2": 120, "f3": 99, "f4": 118, "f5": 96,
         "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103, "f12": 111}


def parse_hotkey(spec: str) -> tuple[int, int]:
    mods, code = 0, None
    for part in spec.lower().replace(" ", "").split("+"):
        if part in CARBON_MODS:
            mods |= CARBON_MODS[part]
        elif part in NAMED:
            code = NAMED[part]
        elif len(part) == 1 and part in LETTERS:
            code = LETTERS.index(part)
        else:
            raise ValueError(f"unknown key in hotkey: {part!r}")
    if code is None:
        raise ValueError("hotkey needs a non-modifier key")
    if mods and not mods & (0x100 | 0x1000):  # macOS 15 refuses Option / Option+Shift alone
        raise ValueError("on macOS the hotkey needs Ctrl or Cmd")
    return mods, code


class EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", c_uint32), ("eventKind", c_uint32)]


class EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", c_uint32), ("id", c_uint32)]


HANDLER = ctypes.CFUNCTYPE(c_int32, c_void_p, c_void_p, c_void_p)
carbon.GetApplicationEventTarget.restype = c_void_p
carbon.InstallEventHandler.restype = c_int32
carbon.RegisterEventHotKey.restype = c_int32
carbon.InstallEventHandler.argtypes = [c_void_p, HANDLER, c_ulong, POINTER(EventTypeSpec), c_void_p,
                                       POINTER(c_void_p)]
carbon.RegisterEventHotKey.argtypes = [c_uint32, c_uint32, EventHotKeyID, c_void_p, c_uint32,
                                       POINTER(c_void_p)]


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode(), "big")


def serve(cfg: dict, on_hotkey) -> int:
    if not posix.claim_single_instance():
        notify("Layout Switcher כבר רץ.")
        return 1
    try:
        mods, code = parse_hotkey(cfg["hotkey"])
    except ValueError as e:
        log.error("bad hotkey in config.json: %s", e)
        notify(f"שגיאה ב-config.json: {e}")
        return 1
    if not accessibility_ok(prompt=True):  # opens the system dialog once
        notify("צריך לאשר ל-Python גישת נגישות: System Settings > Privacy & Security > Accessibility")
    app = msg(cls("NSApplication"), "sharedApplication")
    msg(app, "setActivationPolicy:", 1, restype=c_bool, argtypes=(c_long,))  # accessory: no Dock icon

    def handler(_call, _event, _data) -> int:
        pool = objc.objc_autoreleasePoolPush()
        try:
            on_hotkey()
        except Exception:
            log.exception("hotkey failed")
        finally:
            objc.objc_autoreleasePoolPop(pool)
        return 0

    cb = HANDLER(handler)  # keep a reference for as long as the loop runs
    spec = EventTypeSpec(_fourcc("keyb"), 5)  # kEventClassKeyboard, kEventHotKeyPressed
    target = carbon.GetApplicationEventTarget()
    href, hk = c_void_p(), c_void_p()
    st = carbon.InstallEventHandler(target, cb, 1, byref(spec), None, byref(href))
    if st != 0:
        log.error("InstallEventHandler failed (OSStatus %d)", st)
        return 1
    st = carbon.RegisterEventHotKey(code, mods, EventHotKeyID(_fourcc("LSwi"), 1), target, 0, byref(hk))
    if st != 0:
        log.error("hotkey %s not registered (OSStatus %d)", cfg["hotkey"], st)
        notify(f"הקיצור {cfg['hotkey']} תפוס. שנה אותו ב-config.json.")
        return 1
    signal.signal(signal.SIGINT, signal.SIG_DFL)  # Python's handler never runs inside NSApp run
    msg(app, "run", restype=None)  # until SIGTERM (`switcher.py --stop`) or Ctrl+C
    return 0
