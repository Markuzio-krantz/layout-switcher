"""Windows plumbing: clipboard save/restore, synthetic keys, input-language switch.

No keyboard hook is installed anywhere; keys are only *sent*, after a hotkey press.
"""
from __future__ import annotations

import ctypes
import json
import time
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

user32.OpenClipboard.argtypes = [w.HWND]
user32.GetClipboardData.argtypes = [w.UINT]
user32.GetClipboardData.restype = w.HANDLE
user32.SetClipboardData.argtypes = [w.UINT, w.HANDLE]
user32.SetClipboardData.restype = w.HANDLE
user32.EnumClipboardFormats.argtypes = [w.UINT]
user32.EnumClipboardFormats.restype = w.UINT
user32.RegisterClipboardFormatW.argtypes = [w.LPCWSTR]
user32.RegisterClipboardFormatW.restype = w.UINT
user32.GetClipboardSequenceNumber.restype = w.DWORD
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.MapVirtualKeyW.argtypes = [w.UINT, w.UINT]
user32.MapVirtualKeyW.restype = w.UINT
user32.GetForegroundWindow.restype = w.HWND
user32.GetOpenClipboardWindow.restype = w.HWND
user32.PostMessageW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
kernel32.GlobalAlloc.argtypes = [w.UINT, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = w.HGLOBAL
kernel32.GlobalLock.argtypes = [w.HGLOBAL]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [w.HGLOBAL]
kernel32.GlobalSize.argtypes = [w.HGLOBAL]
kernel32.GlobalSize.restype = ctypes.c_size_t
kernel32.GlobalFree.argtypes = [w.HGLOBAL]
kernel32.GetModuleHandleW.argtypes = [w.LPCWSTR]
kernel32.GetModuleHandleW.restype = w.HMODULE
kernel32.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel32.OpenProcess.restype = w.HANDLE
kernel32.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
kernel32.CloseHandle.argtypes = [w.HANDLE]
user32.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]

CF_UNICODETEXT = 13
# Handles that are GDI objects, not memory: cannot be copied byte-wise (CF_DIB covers bitmaps).
GDI_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
GMEM_MOVEABLE = 0x0002

# Keep our temporary clipboard writes out of Win+V history and cloud sync.
_NO_HISTORY = [
    ("ExcludeClipboardContentFromMonitorProcessing", b"\0"),
    ("CanIncludeInClipboardHistory", (0).to_bytes(4, "little")),
    ("CanUploadToCloudClipboard", (0).to_bytes(4, "little")),
]


def _open_clipboard(tries: int = 40) -> None:
    for _ in range(tries):
        if user32.OpenClipboard(None):
            return
        time.sleep(0.01)
    raise OSError("clipboard is busy")


def _put(fmt: int, data: bytes) -> None:
    h = kernel32.GlobalAlloc(GMEM_MOVEABLE, max(len(data), 1))
    if not h:
        return
    p = kernel32.GlobalLock(h)
    if not p:
        kernel32.GlobalFree(h)
        return
    ctypes.memmove(p, data, len(data))
    kernel32.GlobalUnlock(h)
    if not user32.SetClipboardData(fmt, h):
        kernel32.GlobalFree(h)


def _mark_no_history() -> None:
    for name, data in _NO_HISTORY:
        _put(user32.RegisterClipboardFormatW(name), data)


def save_clipboard() -> list[tuple[int, bytes]]:
    saved = []
    _open_clipboard()
    try:
        fmt = 0
        while fmt := user32.EnumClipboardFormats(fmt):
            if fmt in GDI_FORMATS or 0x200 <= fmt <= 0x3FF:
                continue
            h = user32.GetClipboardData(fmt)
            if not h:
                continue
            size = kernel32.GlobalSize(h)
            p = kernel32.GlobalLock(h)
            if not p:
                continue
            try:
                saved.append((fmt, ctypes.string_at(p, size)))
            finally:
                kernel32.GlobalUnlock(h)
    finally:
        user32.CloseClipboard()
    return saved


def restore_clipboard(saved: list[tuple[int, bytes]]) -> None:
    _open_clipboard(tries=300)  # up to ~3 s: losing the user's clipboard is the worst outcome
    try:
        user32.EmptyClipboard()
        for fmt, data in saved:
            _put(fmt, data)
        _mark_no_history()  # the original is already in history; don't add it twice
    finally:
        user32.CloseClipboard()


def set_text(text: str) -> None:
    _open_clipboard()
    try:
        user32.EmptyClipboard()
        _put(CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
        _mark_no_history()
    finally:
        user32.CloseClipboard()


def _get_format(fmt: int) -> bytes | None:
    h = user32.GetClipboardData(fmt)
    if not h:
        return None
    p = kernel32.GlobalLock(h)
    if not p:
        return None
    try:
        return ctypes.string_at(p, kernel32.GlobalSize(h))
    finally:
        kernel32.GlobalUnlock(h)


def get_text() -> tuple[str | None, bool]:
    """(clipboard text, True if it came from an *empty* selection in VS Code)."""
    _open_clipboard()
    try:
        raw = _get_format(CF_UNICODETEXT)
        text = raw.decode("utf-16-le", "replace").split("\0", 1)[0] if raw else None
        vs = _get_format(user32.RegisterClipboardFormatW("vscode-editor-data"))
        empty_sel = False
        if vs:
            try:
                empty_sel = bool(json.loads(vs.decode("utf-8", "ignore").rstrip("\0"))
                                 .get("isFromEmptySelection"))
            except ValueError:
                pass
        return text, empty_sel
    finally:
        user32.CloseClipboard()


# ---- synthetic keys -------------------------------------------------------
class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
                ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", _INPUTUNION)]


user32.SendInput.argtypes = [w.UINT, ctypes.POINTER(INPUT), ctypes.c_int]

VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN = 0x10, 0x11, 0x12, 0x5B, 0x5C
VK_HOME = 0x24
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, VK_LWIN, VK_RWIN}
KEYUP, EXTKEY, UNICODE = 0x2, 0x1, 0x4


def _key(vk: int, up: bool) -> INPUT:
    flags = (KEYUP if up else 0) | (EXTKEY if vk in EXTENDED else 0)
    return INPUT(type=1, ki=KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, 0))


def send_combo(*vks: int) -> None:
    """Press keys in order, release in reverse — e.g. send_combo(VK_CONTROL, ord('C'))."""
    events = [_key(v, False) for v in vks] + [_key(v, True) for v in reversed(vks)]
    arr = (INPUT * len(events))(*events)
    user32.SendInput(len(events), arr, ctypes.sizeof(INPUT))


def send_unicode(text: str, delay: float = 0.01) -> None:
    """Type text directly, one UTF-16 unit at a time (used by the end-to-end tests)."""
    import array
    units = array.array("H", text.encode("utf-16-le"))
    for code in units:
        events = [INPUT(type=1, ki=KEYBDINPUT(0, code, UNICODE | (KEYUP if up else 0), 0, 0))
                  for up in (False, True)]
        user32.SendInput(2, (INPUT * 2)(*events), ctypes.sizeof(INPUT))
        time.sleep(delay)


def mask_modifiers() -> None:
    """Tap an unassigned key (vkE8) while the hotkey's modifiers are still down, so the
    app never sees a lone Alt/Win press-release (menu, key tips, Start) or Alt+Shift
    (language switch). The hotkey's main key is swallowed by Windows, hence the need."""
    mods = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)
    if any(user32.GetAsyncKeyState(m) & 0x8000 for m in mods):
        send_combo(0xE8)


def wait_modifiers_released(timeout: float = 1.5) -> None:
    """The hotkey's own Ctrl/Alt/Shift must be up, or our Ctrl+C becomes Ctrl+Alt+C."""
    mods = (VK_SHIFT, VK_CONTROL, VK_MENU, VK_LWIN, VK_RWIN)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if not any(user32.GetAsyncKeyState(m) & 0x8000 for m in mods):
            return
        time.sleep(0.01)
    held = [m for m in mods if user32.GetAsyncKeyState(m) & 0x8000]
    events = [_key(m, True) for m in held]
    if events:
        user32.SendInput(len(events), (INPUT * len(events))(*events), ctypes.sizeof(INPUT))


def copy_and_read(timeout: float = 0.5) -> tuple[str | None, bool]:
    """Send Ctrl+C and return what landed on the clipboard, or (None, False) if nothing did."""
    seq = user32.GetClipboardSequenceNumber()
    send_combo(VK_CONTROL, ord("C"))
    end = time.monotonic() + timeout
    while user32.GetClipboardSequenceNumber() == seq:
        if time.monotonic() > end:
            return None, False
        time.sleep(0.01)
    time.sleep(0.03)  # let the app finish writing every format
    return get_text()


# ---- delayed-rendering paste ---------------------------------------------------
# The clipboard is offered *empty* with a promise of text; Windows asks us for the
# text (WM_RENDERFORMAT) at the moment the app actually pastes. Only after that do we
# restore the user's clipboard, so slow apps (Word) never paste the restored content.
LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(LRESULT, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
                ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
                ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON)]


user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.DefWindowProcW.restype = LRESULT
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
user32.CreateWindowExW.argtypes = [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, w.HWND,
                                   w.HMENU, w.HINSTANCE, w.LPVOID]
user32.CreateWindowExW.restype = w.HWND
user32.MsgWaitForMultipleObjects.argtypes = [w.DWORD, ctypes.c_void_p, w.BOOL, w.DWORD, w.DWORD]
user32.PeekMessageW.argtypes = [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT]
WM_RENDERFORMAT, WM_RENDERALLFORMATS = 0x0305, 0x0306
HWND_MESSAGE = w.HWND(-3)


class Paster:
    """Owns a hidden message-only window that answers the app's paste request."""

    def __init__(self) -> None:
        self.text: str | None = None
        self.rendered = False
        self.requesters: list[tuple[float, str]] = []
        self.sent_at = 0.0
        self._proc = WNDPROC(self._wndproc)  # keep a reference: Windows calls it later
        hinst = kernel32.GetModuleHandleW(None)
        wc = WNDCLASSEXW(cbSize=ctypes.sizeof(WNDCLASSEXW), lpfnWndProc=self._proc,
                         hInstance=hinst, lpszClassName="LayoutSwitcherClip")
        user32.RegisterClassExW(ctypes.byref(wc))
        self.hwnd = user32.CreateWindowExW(0, "LayoutSwitcherClip", None, 0, 0, 0, 0, 0,
                                           HWND_MESSAGE, None, hinst, None)

    def _data(self) -> bytes:
        return ((self.text or "") + "\0").encode("utf-16-le")

    def _wndproc(self, hwnd, msg, wparam, lparam):
        if msg == WM_RENDERFORMAT and wparam == CF_UNICODETEXT:
            _put(CF_UNICODETEXT, self._data())  # the requester already holds the clipboard open
            self.requesters.append((time.monotonic(), _window_process(user32.GetOpenClipboardWindow())))
            self.rendered = True
            return 0
        if msg == WM_RENDERALLFORMATS:
            if user32.OpenClipboard(hwnd):
                _put(CF_UNICODETEXT, self._data())
                user32.CloseClipboard()
            return 0
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def paste(self, text: str, timeout: float = 8.0) -> bool:
        """Paste text into the focused app; True once the app has taken it."""
        self.text, self.rendered, self.requesters = text, False, []
        for _ in range(40):
            if user32.OpenClipboard(self.hwnd):
                break
            time.sleep(0.01)
        else:
            raise OSError("clipboard is busy")
        try:
            user32.EmptyClipboard()
            user32.SetClipboardData(CF_UNICODETEXT, None)  # promise, rendered on demand
            _mark_no_history()
        finally:
            user32.CloseClipboard()
        self.sent_at = time.monotonic()
        send_combo(VK_CONTROL, ord("V"))
        msg = w.MSG()
        end = time.monotonic() + timeout
        while not self.rendered and time.monotonic() < end:
            user32.MsgWaitForMultipleObjects(0, None, False, 50, 0x04FF)
            while user32.PeekMessageW(ctypes.byref(msg), self.hwnd, 0, 0, 1):
                user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.15)  # let the app finish inserting before the clipboard changes again
        return self.rendered


def select_to_line_start() -> None:
    send_combo(VK_SHIFT, VK_HOME)


class LineOps:
    """Moves the selection's free end by visual lines (for selection.extend_over_wraps)."""

    def copy(self) -> str | None:
        return copy_and_read()[0]

    def line_up(self) -> None:
        send_combo(VK_SHIFT, 0x26)  # Up
        send_combo(VK_SHIFT, VK_HOME)

    def line_down(self) -> None:
        send_combo(VK_SHIFT, 0x28)  # Down
        send_combo(VK_SHIFT, VK_HOME)


def foreground_process() -> str:
    """Lower-case exe name of the focused window's process, e.g. 'code.exe'."""
    return _window_process(user32.GetForegroundWindow())


def _window_process(hwnd) -> str:
    if not hwnd:
        return ""
    pid = w.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    h = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = w.DWORD(1024)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value.rsplit("\\", 1)[-1].lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def switch_input_language(hkl: int) -> None:
    """Ask the focused window to switch to this layout (like Alt+Shift; per window)."""
    WM_INPUTLANGCHANGEREQUEST = 0x0050
    hwnd = user32.GetForegroundWindow()
    if hwnd:
        user32.PostMessageW(hwnd, WM_INPUTLANGCHANGEREQUEST, 0, hkl)


def beep() -> None:
    user32.MessageBeep(0x30)
