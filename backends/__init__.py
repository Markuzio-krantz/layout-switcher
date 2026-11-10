"""Platform backends. Each module exposes the same small interface:

  configure(cfg)                      read platform options from config.json
  NAME, LOG_DIR, CONSOLES             str, str, set of foreground names where it only beeps
  load_layouts() -> list[Layout]      the user's keyboard layouts, read from the OS
  save_clipboard() / restore_clipboard(saved)
  copy_and_read() -> (text | None, empty_selection)
  Paster().paste(text) -> bool        paste text into the focused app
  select_to_line_start(); LineOps     selection moves (see selection.py)
  foreground_process() -> str         lower-case name of the focused app
  mask_modifiers(); wait_modifiers_released()
  switch_input_language(handle)       Layout.hkl of the target layout
  beep(); notify(text)
  serve(cfg, on_hotkey) -> int        block, calling on_hotkey() per press
  stop() -> int                       stop a running instance
  trigger() -> bool                   hand one press to a running instance (`--convert`)
"""
from __future__ import annotations

import sys
from types import ModuleType


def get() -> ModuleType:
    if sys.platform == "win32":
        from . import windows as mod
    elif sys.platform == "darwin":
        from . import macos as mod
    elif sys.platform.startswith("linux") or "bsd" in sys.platform:
        from . import linux as mod
    else:
        raise OSError(f"unsupported platform: {sys.platform}")
    return mod
