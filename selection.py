"""Grow the no-selection prefix across soft-wrapped lines.

Shift+Home only reaches the start of the *visual* line. When an editor wraps a
long line, the start of a wrong-layout run can sit on the visual line above, so
the run would be cut in half. While the run reaches the very start of what is
selected, extend one visual line up (Shift+Up, Shift+Home) and look again. Stop at
a different alphabet, at a real line break (then step back down), or when the
selection stops growing (start of the document).

`ops` keeps this testable without a real editor: it needs
  copy() -> str | None      text currently selected
  line_up() / line_down()   Shift+Up+Shift+Home / Shift+Down+Shift+Home
"""
from __future__ import annotations

from typing import Protocol

from convert import Layout, choose_run

BREAKS = ("\n", "\r", " ")


class SelectionOps(Protocol):
    def copy(self) -> str | None: ...
    def line_up(self) -> None: ...
    def line_down(self) -> None: ...


def extend_over_wraps(prefix: str, layouts: list[Layout], ops: SelectionOps,
                      max_lines: int = 8) -> str:
    """Return the text that is selected when done (always matches the live selection)."""
    for _ in range(max_lines):
        run = choose_run(prefix, layouts)
        if run is None or run[0] > 0:
            return prefix  # the run starts inside what we already see
        ops.line_up()
        grown = ops.copy()
        if not grown or len(grown) <= len(prefix) or not grown.endswith(prefix):
            return grown if grown else prefix  # no growth: start of the document
        added = grown[: len(grown) - len(prefix)]
        if any(b in added for b in BREAKS):
            ops.line_down()  # crossed a real line break: undo that step
            back = ops.copy()
            return back if back else prefix
        prefix = grown
    return prefix
