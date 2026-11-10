"""Linux backend (X11 and Wayland). Same job as the Windows one, through small tools:

  layouts    libxkbcommon (ctypes, installed with every desktop) on the layout list from
             config.json "xkb_layouts", GNOME / KDE settings, $XKB_DEFAULT_LAYOUT
             or `setxkbmap -query`
  clipboard  X11: xclip (or xsel)   Wayland: wl-clipboard (wl-copy / wl-paste)
  keys       X11: xdotool           Wayland: wtype (wlroots: sway, Hyprland...),
                                    else ydotool (needs the ydotoold service)
  hotkey     a desktop shortcut runs `switcher.py --convert`, which hands the press to
             the running instance over a private Unix socket. No keyboard is read.

Copy and paste use Ctrl+Insert / Shift+Insert: same as Ctrl+C / Ctrl+V in GTK, Qt,
browsers and editors, and harmless in a terminal (Ctrl+C would kill the program there).
"""
from __future__ import annotations

import ctypes
import ctypes.util
import functools
import logging
import os
import re
import subprocess
import time
import uuid

from convert import Layout

from . import posix
from .posix import stop  # noqa: F401  (backend interface)

NAME = "linux"
log = logging.getLogger("LayoutSwitcher")
LOG_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"),
                       "layout-switcher")
CONSOLES = {"gnome-terminal-", "gnome-terminal-server", "konsole", "xterm", "uxterm", "alacritty",
            "kitty", "tilix", "terminator", "xfce4-terminal", "wezterm-gui", "foot", "urxvt",
            "rxvt", "lxterminal", "mate-terminal", "terminology", "qterminal", "ptyxis",
            "guake", "yakuake", "st", "kgx", "cool-retro-term", "deepin-terminal"}

# physical typing keys, as evdev codes (= the Windows scancodes); xkb keycode = code + 8
KEYCODES = (tuple(range(2, 14)) + tuple(range(16, 28)) + tuple(range(30, 42))
            + (43,) + tuple(range(44, 54)) + (86,))
LANG = {"us": "en", "gb": "en", "au": "en", "il": "he", "ru": "ru", "ua": "uk", "by": "be",
        "de": "de", "fr": "fr", "es": "es", "it": "it", "pt": "pt", "br": "pt", "ara": "ar",
        "gr": "el", "pl": "pl", "tr": "tr", "bg": "bg", "kz": "kk", "ir": "fa", "am": "hy",
        "ge": "ka", "cz": "cs", "sk": "sk", "rs": "sr", "nl": "nl", "se": "sv", "fi": "fi"}


def session() -> str:
    t = os.environ.get("XDG_SESSION_TYPE", "")
    if t == "x11":
        return "x11"
    if t == "wayland" or os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    return "x11"


def _desktop() -> str:
    return os.environ.get("XDG_CURRENT_DESKTOP", "").lower()


# ---- layouts -------------------------------------------------------------------------
def _split(item: str) -> tuple[str, str]:
    """'il', 'ru+phonetic' or 'ru(phonetic)' -> (layout, variant)."""
    m = re.fullmatch(r"\s*([\w-]+)\s*(?:[+(]\s*([\w-]+)\s*\)?)?\s*", item)
    return (m.group(1), m.group(2) or "") if m else (item.strip(), "")


def configured_layouts(override: list[str] | str | None = None) -> list[tuple[str, str]]:
    """The user's layouts in switching order, as (xkb layout, variant)."""
    if override:
        items = override.split(",") if isinstance(override, str) else override
        return [_split(i) for i in items if i.strip()]
    r = posix.run(["gsettings", "get", "org.gnome.desktop.input-sources", "sources"])
    if r and r.returncode == 0:
        found = re.findall(r"\(\s*'xkb'\s*,\s*'([^']+)'\s*\)", r.stdout.decode())
        if found:
            return [_split(x) for x in found]
    for tool in ("kreadconfig6", "kreadconfig5"):
        r = posix.run([tool, "--file", "kxkbrc", "--group", "Layout", "--key", "LayoutList"])
        if r and r.returncode == 0 and r.stdout.strip():
            lays = r.stdout.decode().strip().split(",")
            v = posix.run([tool, "--file", "kxkbrc", "--group", "Layout", "--key", "VariantList"])
            vars_ = v.stdout.decode().strip().split(",") if v and v.returncode == 0 else []
            return [(l.strip(), vars_[i].strip() if i < len(vars_) else "") for i, l in enumerate(lays)]
    env = os.environ.get("XKB_DEFAULT_LAYOUT")  # set by sway & co. from their config
    if env:
        return [_split(x) for x in env.split(",")]
    r = posix.run(["setxkbmap", "-query"])
    if r and r.returncode == 0:
        q = dict(re.findall(r"^(\w+):\s*(.*)$", r.stdout.decode(), re.M))
        if q.get("layout"):
            lays, vars_ = q["layout"].split(","), q.get("variant", "").split(",")
            return [(l, vars_[i] if i < len(vars_) else "") for i, l in enumerate(lays)]
    return []


class _RuleNames(ctypes.Structure):
    _fields_ = [(n, ctypes.c_char_p) for n in ("rules", "model", "layout", "variant", "options")]


_xkb = None


def _libxkb():
    global _xkb
    if _xkb is None:
        path = ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
        x = ctypes.CDLL(path)
        vp, u32 = ctypes.c_void_p, ctypes.c_uint32
        for fn, res, args in (
            ("xkb_context_new", vp, [ctypes.c_int]),
            ("xkb_context_unref", None, [vp]),
            ("xkb_keymap_new_from_names", vp, [vp, ctypes.POINTER(_RuleNames), ctypes.c_int]),
            ("xkb_keymap_unref", None, [vp]),
            ("xkb_keymap_mod_get_index", u32, [vp, ctypes.c_char_p]),
            ("xkb_state_new", vp, [vp]),
            ("xkb_state_unref", None, [vp]),
            ("xkb_state_update_mask", ctypes.c_int, [vp, u32, u32, u32, u32, u32, u32]),
            ("xkb_state_key_get_utf32", u32, [vp, u32]),
        ):
            f = getattr(x, fn)
            f.restype, f.argtypes = res, args
        _xkb = x
    return _xkb


@functools.lru_cache(maxsize=32)
def keymap_chars(layout: str, variant: str = "") -> dict[tuple[int, bool], str]:
    """What each typing key gives in this xkb layout, plain and with Shift."""
    x = _libxkb()
    ctx = x.xkb_context_new(0)
    if not ctx:
        raise OSError("xkb_context_new failed")
    try:
        names = _RuleNames(b"evdev", b"pc105", layout.encode(), variant.encode(), None)
        km = x.xkb_keymap_new_from_names(ctx, ctypes.byref(names), 0)
        if not km:
            raise ValueError(f"unknown xkb layout {layout}({variant})")
        try:
            st = x.xkb_state_new(km)
            shift = 1 << x.xkb_keymap_mod_get_index(km, b"Shift")
            keys = {}
            for want_shift in (False, True):
                x.xkb_state_update_mask(st, shift if want_shift else 0, 0, 0, 0, 0, 0)
                for code in KEYCODES:
                    cp = x.xkb_state_key_get_utf32(st, code + 8)
                    ch = chr(cp) if cp else ""
                    if ch and ch.isprintable() and not ch.isspace():
                        keys[(code, want_shift)] = ch
            x.xkb_state_unref(st)
            return keys
        finally:
            x.xkb_keymap_unref(km)
    finally:
        x.xkb_context_unref(ctx)


_override: list[str] | str | None = None  # config.json "xkb_layouts"


def configure(cfg: dict) -> None:
    global _override
    _override = cfg.get("xkb_layouts") or None


def load_layouts() -> list[Layout]:
    """Layout.hkl is the layout's index in the switching order."""
    out, seen = [], set()
    for i, (lay, var) in enumerate(configured_layouts(_override)):
        try:
            keys = keymap_chars(lay, var)
        except (OSError, ValueError) as e:
            log.warning("layout %s(%s) skipped: %s", lay, var, e)
            continue
        name = LANG.get(lay, lay)
        if name in seen:
            name = f"{lay}({var})" if var else lay
        seen.add(name)
        out.append(Layout(name, i, keys))
    return out


# ---- keys ------------------------------------------------------------------------------
EVDEV = {"ctrl": 29, "shift": 42, "alt": 56, "super": 125, "Insert": 110, "Home": 102,
         "Up": 103, "Down": 108}
COMBOS = {"copy": ("ctrl", "Insert"), "paste": ("shift", "Insert"), "home": ("shift", "Home"),
          "up": ("shift", "Up"), "down": ("shift", "Down")}
_wtype_ok: bool | None = None
_ydotool_old: bool | None = None


def send(combo: str) -> None:
    global _wtype_ok, _ydotool_old
    mod, key = COMBOS[combo]
    if session() == "x11":
        posix.run(["xdotool", "key", "--clearmodifiers", f"{mod}+{key}"])
        return
    if _wtype_ok is not False and posix.have("wtype"):
        wmod = "logo" if mod == "super" else mod
        r = posix.run(["wtype", "-M", wmod, "-k", key, "-m", wmod])
        _wtype_ok = bool(r and r.returncode == 0)
        if _wtype_ok:
            return
    if _ydotool_old is not True:
        m, k = EVDEV[mod], EVDEV[key]
        r = posix.run(["ydotool", "key", f"{m}:1", f"{k}:1", f"{k}:0", f"{m}:0"])
        if r and r.returncode == 0:
            _ydotool_old = False
            return
    r = posix.run(["ydotool", "key", f"{mod}+{key}"])  # ydotool 0.1.x syntax
    _ydotool_old = bool(r and r.returncode == 0)
    if not _ydotool_old:
        log.error("no way to send keys: install wtype (sway/Hyprland) or ydotool + ydotoold")


def mask_modifiers() -> None:
    pass  # only Windows reacts to a lone Alt / Win release


_x11 = None


def _x11_keymap_has_modifiers() -> bool | None:
    """True while Shift/Ctrl/Alt/Super is held (X11 only), None if it cannot tell."""
    global _x11
    try:
        if _x11 is None:
            lib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            lib.XOpenDisplay.restype = ctypes.c_void_p
            lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            lib.XQueryKeymap.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            lib.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
            lib.XKeysymToKeycode.restype = ctypes.c_ubyte
            dpy = lib.XOpenDisplay(None)
            if not dpy:
                return None
            codes = {lib.XKeysymToKeycode(dpy, s) for s in range(0xFFE1, 0xFFED)} - {0}
            _x11 = (lib, dpy, codes)
        lib, dpy, codes = _x11
        buf = ctypes.create_string_buffer(32)
        lib.XQueryKeymap(dpy, buf)
        return any(buf.raw[c // 8] >> (c % 8) & 1 for c in codes)
    except OSError:
        return None


def wait_modifiers_released(timeout: float = 1.5) -> None:
    """The shortcut's own Ctrl/Shift must be up before we send Shift+Home and friends."""
    if session() == "x11":
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            held = _x11_keymap_has_modifiers()
            if not held:
                return
            time.sleep(0.01)
        return  # xdotool --clearmodifiers lifts whatever is still held
    # Wayland lets no program read the keyboard. wtype sends its own modifier state,
    # ydotool shares the physical one: give the hand time to leave the keys.
    time.sleep(0.05 if _wtype_ok else 0.35)


def select_to_line_start() -> None:
    send("home")


class LineOps:
    def copy(self) -> str | None:
        return copy_and_read()[0]

    def line_up(self) -> None:
        send("up")
        send("home")

    def line_down(self) -> None:
        send("down")
        send("home")


# ---- clipboard ------------------------------------------------------------------------
TEXT_TYPES = ("text/plain;charset=utf-8", "UTF8_STRING", "text/plain", "STRING", "TEXT")


def _feed(cmd: list[str], data: bytes) -> None:
    """For xclip -i / wl-copy, which stay in the background to serve the data: no pipes
    on their stdout/stderr, or we would wait for them forever."""
    try:
        subprocess.run(cmd, input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=2)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("%s failed: %s", cmd[0], e)


def _types(primary: bool = False) -> list[str]:
    if session() == "wayland":
        r = posix.run(["wl-paste", "--list-types"] + (["--primary"] if primary else []))
    elif posix.have("xclip"):
        r = posix.run(["xclip", "-selection", "primary" if primary else "clipboard", "-t", "TARGETS", "-o"])
    else:
        return list(TEXT_TYPES[:1])  # xsel: text only
    return r.stdout.decode(errors="replace").split() if r and r.returncode == 0 else []


def _read(mime: str, primary: bool = False) -> bytes | None:
    if session() == "wayland":
        r = posix.run(["wl-paste", "--no-newline", "--type", mime] + (["--primary"] if primary else []))
    elif posix.have("xclip"):
        r = posix.run(["xclip", "-selection", "primary" if primary else "clipboard", "-t", mime, "-o"])
    else:
        r = posix.run(["xsel", "--primary" if primary else "--clipboard", "--output"])
    return r.stdout if r and r.returncode == 0 else None


def _write(data: bytes, mime: str = "text/plain;charset=utf-8", primary: bool = False) -> None:
    if session() == "wayland":
        _feed(["wl-copy", "--type", mime] + (["--primary"] if primary else []), data)
    elif posix.have("xclip"):
        xmime = "UTF8_STRING" if mime.startswith("text/plain") else mime
        _feed(["xclip", "-selection", "primary" if primary else "clipboard", "-t", xmime, "-i"], data)
    else:
        _feed(["xsel", "--primary" if primary else "--clipboard", "--input"], data)


def get_text() -> str | None:
    types = _types()
    mime = next((t for t in TEXT_TYPES if t in types), None)
    if mime is None:
        return None
    raw = _read(mime)
    return raw.decode("utf-8", "replace") if raw is not None else None


def set_text(text: str) -> None:
    _write(text.encode("utf-8"))  # never PRIMARY: taking it makes GTK drop the selection


def save_clipboard() -> tuple[str, bytes] | None:
    """The clipboard as one format: text if there is any, else an image. The tools can
    serve only one format at a time, so rich text comes back as plain text."""
    types = _types()
    for want in TEXT_TYPES + ("image/png", "image/jpeg", "text/uri-list"):
        if want in types:
            data = _read(want)
            if data is not None:
                return want, data
    return None


def restore_clipboard(saved: tuple[str, bytes] | None) -> None:
    if saved is None:
        if session() == "wayland":
            posix.run(["wl-copy", "--clear"])
        else:
            _write(b"")
        return
    mime, data = saved
    _write(data, "text/plain;charset=utf-8" if mime in TEXT_TYPES else mime)


def copy_and_read(timeout: float = 0.5) -> tuple[str | None, bool]:
    """Send copy and return the text that landed, or (None, False) if nothing did.
    There is no change counter on Linux, so a random marker goes in first."""
    marker = f"layout-switcher-{uuid.uuid4().hex}"
    set_text(marker)
    send("copy")
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        text = get_text()
        if text is not None and text != marker:
            return text, False
        time.sleep(0.02)
    return None, False


class Paster:
    """Shift+Insert pastes CLIPBOARD in GTK, Qt, Chromium, Firefox and Electron apps."""

    def __init__(self) -> None:
        self.sent_at = 0.0
        self.requesters: list[tuple[float, str]] = []

    def paste(self, text: str, settle: float = 0.25) -> bool:
        set_text(text)
        self.sent_at = time.monotonic()
        send("paste")
        time.sleep(settle)  # no paste notification on Linux: give the app time to read it
        return True


# ---- focused app, language switch, feedback ---------------------------------------
def foreground_process() -> str:
    """Process name of the focused window on X11; '' on Wayland (no way to ask)."""
    if session() != "x11":
        return ""
    r = posix.run(["xdotool", "getactivewindow", "getwindowpid"])
    pid = r.stdout.decode().strip() if r and r.returncode == 0 else ""
    if not pid.isdigit():
        return ""
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip().lower()
    except OSError:
        return ""


def switch_input_language(index: int) -> None:
    """KDE: D-Bus. sway: swaymsg. X11: XkbLockGroup. GNOME on Wayland offers no way."""
    if "kde" in _desktop():
        posix.run(["dbus-send", "--session", "--dest=org.kde.keyboard", "--type=method_call",
                   "/Layouts", "org.kde.KeyboardLayouts.setLayout", f"uint32:{index}"])
    elif os.environ.get("SWAYSOCK"):
        posix.run(["swaymsg", "input", "type:keyboard", "xkb_switch_layout", str(index)])
    elif session() == "x11" and index < 4:
        try:
            lib = ctypes.CDLL(ctypes.util.find_library("X11") or "libX11.so.6")
            lib.XOpenDisplay.restype = ctypes.c_void_p
            lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            lib.XkbLockGroup.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint]
            lib.XCloseDisplay.argtypes = [ctypes.c_void_p]
            dpy = lib.XOpenDisplay(None)
            if dpy:
                lib.XkbLockGroup(dpy, 0x100, index)  # XkbUseCoreKbd
                lib.XCloseDisplay(dpy)  # flushes the request
        except OSError as e:
            log.debug("XkbLockGroup: %s", e)


def beep() -> None:
    if not posix.run(["canberra-gtk-play", "--id=bell"]):
        log.info("beep (install libcanberra-gtk3-module or sound-theme-freedesktop to hear it)")


def notify(text: str) -> None:
    if not posix.run(["notify-send", "Layout Switcher", text]):
        print(text)


# ---- the running instance and the `--convert` trigger ------------------------------
def serve(cfg: dict, on_hotkey) -> int:
    return posix.serve_socket(on_hotkey, notify)


trigger = posix.trigger
