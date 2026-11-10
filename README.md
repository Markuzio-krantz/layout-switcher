# layout-switcher

Typed in the wrong keyboard layout? One hotkey (`Ctrl+Shift+Space`) converts the
text between the layouts installed in Windows (tested with English, Hebrew
Standard and Russian): `akuo` -> `שלום` -> `флгщ` -> back to `akuo`.

- With a selection: converts the selection.
- Without one: converts the trailing run of the line that is in one alphabet,
  including when the editor soft-wrapped it onto the line above.
- Pure Python 3.9+ standard library (ctypes). No keyboard hook, no network,
  the clipboard is restored after every press.

## Install (Windows, one line in cmd)

```
curl -L -o "%TEMP%\ls.zip" https://github.com/Markuzio-krantz/layout-switcher/archive/refs/heads/main.zip && tar -xf "%TEMP%\ls.zip" -C "%TEMP%" && python "%TEMP%\layout-switcher-main\install.py"
```

Or with git:

```
git clone https://github.com/Markuzio-krantz/layout-switcher && cd layout-switcher && python install.py
```

`install.py` copies the tool to `%LOCALAPPDATA%\Programs\LayoutSwitcher`, adds one
HKCU `Run` value so it starts with Windows, and starts it.
`python install.py --no-autostart` skips the login entry,
`python install.py --uninstall` removes everything.

Tests: `python -m unittest discover -s tests`

Full Hebrew instructions: [README-he.md](README-he.md)
