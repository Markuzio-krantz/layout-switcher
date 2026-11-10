# layout-switcher

Typed in the wrong keyboard layout? One hotkey (`Ctrl+Shift+Space`) converts the
text between the layouts you have installed (tested with English, Hebrew and
Russian): `akuo` -> `שלום` -> `флгщ` -> back to `akuo`.

- With a selection: converts the selection.
- Without one: converts the trailing run of the line that is in one alphabet,
  including when the editor soft-wrapped it onto the line above.
- Press again within 10 seconds: next language; the last press restores the original.
- Pure Python 3.9+ standard library. No keyboard hook, no network, the clipboard
  is restored after every press. Key maps are read from the system, nothing is hard-coded.

Full Hebrew instructions: [README-he.md](README-he.md)

## Install

Each line downloads this repository's `main` branch and runs `install.py`, which
copies the tool to a per-user folder, starts it at login and starts it now.
No admin rights. `install.py --no-autostart` skips the login entry,
`install.py --uninstall` removes everything.

### Windows (cmd)

```
curl -L -o "%TEMP%\ls.zip" https://github.com/Markuzio-krantz/layout-switcher/archive/refs/heads/main.zip && "%SystemRoot%\System32\tar.exe" -xf "%TEMP%\ls.zip" -C "%TEMP%" && python "%TEMP%\layout-switcher-main\install.py"
```

(`tar.exe` is named in full because Git for Windows can put its own `tar` first on PATH, and that one cannot read zip files.)
Installs to `%LOCALAPPDATA%\Programs\LayoutSwitcher` with one HKCU `Run` value.

### macOS and Linux (Terminal)

```
curl -fsSL https://github.com/Markuzio-krantz/layout-switcher/archive/refs/heads/main.tar.gz | tar -xz -C /tmp && python3 /tmp/layout-switcher-main/install.py
```

Or with git, on any system:

```
git clone https://github.com/Markuzio-krantz/layout-switcher && cd layout-switcher && python3 install.py
```

## macOS

**Not tested on a real Mac yet** - it was written and checked on Windows and Linux only.
Reports welcome.

- Python: the `python3` that ships with the Xcode Command Line Tools (3.9) is enough;
  macOS offers to install it the first time you type `python3`.
- Installs to `~/Library/Application Support/LayoutSwitcher` and a LaunchAgent
  `~/Library/LaunchAgents/io.github.markuzio-krantz.layout-switcher.plist`.
- **Accessibility permission is required**: macOS drops keystrokes sent by programs
  that do not have it. On first start the system asks; otherwise open System Settings >
  Privacy & Security > Accessibility and switch on *Python* (the log, below, names the
  exact binary). Then stop and start the tool.
- Hotkey: `Ctrl+Shift+Space`, registered with Carbon `RegisterEventHotKey` (the system
  reports that one combination, nothing else). Change it in `config.json`, e.g. `cmd+shift+space`.
- Uses Cmd+Shift+Left to select to line start, Cmd+C, Cmd+V. Key maps come from the
  input sources enabled in System Settings > Keyboard (UCKeyTranslate).
- If the hotkey does not fire, bind `python3 ".../LayoutSwitcher/switcher.py" --convert`
  to a key with the Shortcuts app ("Run Shell Script"). That runs one conversion per
  press (no 3-step cycle) and probably needs the Accessibility permission for Shortcuts too.
- Stop / start: `python3 ~/Library/Application\ Support/LayoutSwitcher/switcher.py --stop`,
  `launchctl kickstart gui/$UID/io.github.markuzio-krantz.layout-switcher`.
- Log: `~/Library/Logs/layout-switcher/switcher.log`.
- Known risks, found in a desk review: Cmd+C / Cmd+V are sent as the QWERTY key positions,
  so with Dvorak as the active layout some apps may misread them; the clipboard is put
  back after a fixed 0.3 s; the Accessibility grant belongs to whatever launched Python
  (the LaunchAgent's Python, Terminal, or Shortcuts), so each may need its own switch.

## Linux

Works on X11 and Wayland, with different helpers. Linux has no common global-hotkey
API that also works under Wayland, so the hotkey is a **desktop shortcut** that runs
`switcher.py --convert`; the installer creates it on GNOME (Settings > Keyboard >
Custom Shortcuts shows it), elsewhere it prints the command to bind.

Install the helpers first (package names are the same on Ubuntu/Debian `apt`,
Fedora `dnf` and Arch `pacman`):

| Session | Needs | What works |
|---|---|---|
| X11 (any desktop) | `xdotool xclip` | everything; tested end-to-end (GTK app on Xvfb, 9/9) |
| Wayland, sway / Hyprland / other wlroots | `wl-clipboard wtype` | conversion and cycling; layout switch after conversion on sway only |
| Wayland, GNOME or KDE | `wl-clipboard ydotool` + the `ydotoold` service with access to `/dev/uinput` | conversion and cycling; layout switch after conversion on KDE only, not on GNOME |

Honest limits:
- **Wayland was not tested end-to-end** (only its pieces). Under Wayland no program can
  ask which app is focused, so terminals are not detected and VS Code's "copy the whole
  line" is not recognised. With ydotool, release the shortcut's keys quickly: the
  keys it sends may combine with keys you still hold.
- Copy and paste are sent as `Ctrl+Insert` / `Shift+Insert`: the same as Ctrl+C / Ctrl+V
  in GTK, Qt, browsers and Electron editors, and harmless in a terminal.
- The clipboard is put back as one format: text if it had text, else an image (xclip and
  wl-copy serve one format at a time), so rich text comes back as plain text.
- The layout list comes from GNOME / KDE settings, `$XKB_DEFAULT_LAYOUT` or
  `setxkbmap -query`; set `"xkb_layouts": "us,il,ru"` in `config.json` to override.
  Key maps are compiled from those names with libxkbcommon.
- Files: `~/.local/share/layout-switcher`, `~/.config/autostart/layout-switcher.desktop`,
  log in `~/.local/state/layout-switcher/switcher.log`.
  Stop: `python3 ~/.local/share/layout-switcher/switcher.py --stop`.

## Tests

```
python -m unittest discover -s tests          # any OS: conversion and wrap logic
python3 tests/e2e_linux.py                    # Linux: real GTK app on a private Xvfb screen
python tests/e2e_apps.py notepad chrome vscode word   # Windows: takes over the keyboard for minutes
```
