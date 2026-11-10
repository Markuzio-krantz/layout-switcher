"""Read the keyboard layouts the user has and build Layout maps from them.

Nothing is hard-coded: every key's character comes from the OS (ToUnicodeEx on
Windows, UCKeyTranslate on macOS, libxkbcommon on Linux), so a layout change is
picked up on the next press. The platform code is in backends/.
Run `python layouts.py --dump FILE` to snapshot the maps (used as test fixture).
"""
from __future__ import annotations

import json
import sys

from convert import Layout


def load_layouts() -> list[Layout]:
    import backends
    return backends.get().load_layouts()


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
        h = d["hkl"]
        print(name, hex(h & 0xFFFFFFFF) if isinstance(h, int) and abs(h) > 0xFF else h,
              "".join(v for k, v in d["keys"].items() if "+" not in k))
