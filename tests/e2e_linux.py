"""End-to-end check on Linux/X11, on a private virtual screen (Xvfb), so it never
touches your own desktop, keyboard or clipboard.

  python3 tests/e2e_linux.py [--debug]

Needs Xvfb, xdotool, xclip and python3-gi + GTK 3 (Ubuntu: `sudo apt install xvfb
xdotool xclip python3-gi gir1.2-gtk-3.0`). There is no Wayland variant: in a headless
sway, GTK's copy never reached the clipboard, so the result would say nothing.
Starts Xvfb, a GTK text box, and the switcher; each press
is `switcher.py --convert`, exactly what a desktop shortcut runs.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DISPLAY = ":87"

# (text in the box, select all first?, [expected after press 1, press 2, ...])
CASES = [
    ("Akuo xu", True, ["שלום סו"]),
    ("אני כותב akuo", False, ["אני כותב שלום", "אני כותב флгщ", "אני כותב akuo"]),
    ("יקךךם", True, ["hello"]),
    ("Hi שלום", False, ["Hi akuo", "Hi флгщ"]),
    ("Руддщ црщ", False, ["יקךךם 'ים", "Hello who"]),
]


def app() -> None:
    """The GTK text box: reads SET <json> / SELECTALL / GET from stdin."""
    import threading

    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import GLib, Gtk

    win = Gtk.Window(title="LSTestBox")
    view = Gtk.TextView()
    win.add(view)
    win.set_default_size(600, 200)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    buf = view.get_buffer()

    def handle(line: str) -> bool:
        cmd, _, arg = line.partition(" ")
        if cmd == "SET":
            buf.set_text(json.loads(arg))
            buf.place_cursor(buf.get_end_iter())
        elif cmd == "SELECTALL":
            buf.select_range(buf.get_start_iter(), buf.get_end_iter())
        elif cmd == "GET":
            text = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), True)
            print(json.dumps(text), flush=True)
        view.grab_focus()
        return False

    def reader() -> None:
        for line in sys.stdin:
            GLib.idle_add(handle, line.strip())
        GLib.idle_add(Gtk.main_quit)

    threading.Thread(target=reader, daemon=True).start()
    Gtk.main()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="lsw-e2e-")
    os.chmod(tmp, 0o700)
    env = {k: v for k, v in os.environ.items()
           if k not in ("WAYLAND_DISPLAY", "DISPLAY", "XDG_CURRENT_DESKTOP")}
    env.update(XDG_RUNTIME_DIR=tmp, XDG_STATE_HOME=tmp, NO_AT_BRIDGE="1", XKB_DEFAULT_LAYOUT="us,il,ru")
    env.update(DISPLAY=DISPLAY, XDG_SESSION_TYPE="x11", GDK_BACKEND="x11")
    server = ["Xvfb", DISPLAY, "-screen", "0", "1024x768x24", "-nolisten", "tcp"]
    procs = [subprocess.Popen(server, env=env, stdout=subprocess.DEVNULL,
                              stderr=open(os.path.join(tmp, "server.log"), "w"))]
    fails = []
    try:
        time.sleep(1.5)
        # The switcher reads the layout list from XKB_DEFAULT_LAYOUT here: this Xvfb build
        # ignores setxkbmap. Text goes in through GTK, so the server keymap does not matter.
        box = subprocess.Popen([sys.executable, __file__, "--app"], env=env, text=True,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        procs.append(box)
        sw = subprocess.Popen([sys.executable, os.path.join(ROOT, "switcher.py")]
                              + (["--debug"] if "--debug" in sys.argv else []), env=env)
        procs.append(sw)
        time.sleep(1.5)
        subprocess.run(["xdotool", "search", "--sync", "--name", "LSTestBox", "windowfocus", "--sync"],
                       env=env, check=True, timeout=10)

        def send(line: str) -> None:
            box.stdin.write(line + "\n")
            box.stdin.flush()

        def get() -> str:
            send("GET")
            return json.loads(box.stdout.readline())

        def clip(value: str | None = None) -> str:
            if value is not None:
                subprocess.run(["xclip", "-selection", "clipboard", "-i"], input=value.encode(), env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return value
            r = subprocess.run(["xclip", "-selection", "clipboard", "-o"], env=env, capture_output=True)
            return r.stdout.decode()

        for n, (typed, select, expected) in enumerate(CASES):
            send("SET " + json.dumps(typed))
            if select:
                send("SELECTALL")
            time.sleep(0.2)
            for press, want in enumerate(expected, 1):
                sentinel = clip(f"user clipboard {n}.{press}")
                t0 = time.monotonic()
                subprocess.run([sys.executable, os.path.join(ROOT, "switcher.py"), "--convert"], env=env,
                               timeout=30)
                took = time.monotonic() - t0
                got = get()
                restored = clip() == sentinel
                ok = got == want and restored
                print(f"{'ok ' if ok else 'FAIL'} {typed!r} press {press}: {got!r}"
                      f"{'' if restored else ' (clipboard NOT restored)'}  {took:.2f}s")
                if not ok:
                    fails.append(f"{typed!r} press {press}: got {got!r}, want {want!r}")
    finally:
        if len(procs) > 2:
            subprocess.run([sys.executable, os.path.join(ROOT, "switcher.py"), "--stop"], env=env)
        for p in reversed(procs):
            p.terminate()
    if fails:
        for name in ("layout-switcher/switcher.log", "server.log"):
            path = os.path.join(tmp, name)
            if os.path.exists(path):
                print(f"--- {name} (tail)")
                print("".join(open(path, encoding="utf-8", errors="replace").readlines()[-12:]))
    print("\nRESULT:", "ALL PASSED" if not fails else f"{len(fails)} failed")
    return 1 if fails else 0


if __name__ == "__main__":
    if "--app" in sys.argv:
        app()
    else:
        sys.exit(main())
