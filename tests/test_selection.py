"""extend_over_wraps against a simulated editor that soft-wraps lines."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from layouts import from_json  # noqa: E402
from selection import extend_over_wraps  # noqa: E402

with open(os.path.join(HERE, "fixture_layouts.json"), encoding="utf-8") as f:
    LAYOUTS = from_json(json.load(f))


class WrappingEditor:
    """Text with real '\n' breaks, soft-wrapped at `width` columns on spaces.
    The selection anchor is the caret at the end; `focus` moves like a real editor."""

    def __init__(self, text: str, width: int):
        self.text, self.width = text, width
        self.lines = self._visual_lines()          # list of (start, end) offsets
        self.focus = len(text)
        self.focus = self._line_start(self._line_of(self.focus))  # after Shift+Home

    def _visual_lines(self):
        out, pos = [], 0
        for para in self.text.split("\n"):
            start = pos
            while len(para) - (start - pos) > self.width:
                seg = self.text[start:start + self.width]
                cut = seg.rfind(" ")
                cut = self.width if cut <= 0 else cut + 1  # the space stays on the upper line
                out.append((start, start + cut))
                start += cut
            out.append((start, pos + len(para)))
            pos += len(para) + 1
        return out

    def _line_of(self, off):
        for i, (a, b) in enumerate(self.lines):
            if a <= off <= b:
                return i
        return len(self.lines) - 1

    def _line_start(self, i):
        return self.lines[i][0]

    def copy(self):
        return self.text[self.focus:len(self.text)] or None

    def line_up(self):
        i = self._line_of(self.focus)
        self.focus = self._line_start(i - 1) if i > 0 else 0

    def line_down(self):
        i = self._line_of(self.focus)
        if i + 1 < len(self.lines):
            self.focus = self._line_start(i + 1)


def run(text, width):
    ed = WrappingEditor(text, width)
    return extend_over_wraps(ed.copy(), LAYOUTS, ed), ed


class Wraps(unittest.TestCase):
    def test_run_split_by_wrap_is_joined(self):
        # Mark's case: Hebrew, then Cyrillic gibberish that wraps onto a second line
        text = "אם אני כותב гея снпгб лфгкм"
        got, _ = run(text, 18)
        self.assertTrue(got.endswith("гея снпгб лфгкм"))
        self.assertIn("כותב", got)  # stopped once the Hebrew word came into view

    def test_no_wrap_needs_no_extension(self):
        got, _ = run("שלום akuo", 80)
        self.assertEqual(got, "שלום akuo")

    def test_stops_at_real_line_break(self):
        text = "akuo nv\nekuo xu ekuo xu"
        got, ed = run(text, 9)
        self.assertEqual(got, "ekuo xu ekuo xu")  # second paragraph only
        self.assertNotIn("\n", got)

    def test_document_start(self):
        got, _ = run("akuo nv ekuo xu ekuo", 9)
        self.assertEqual(got, "akuo nv ekuo xu ekuo")


if __name__ == "__main__":
    unittest.main()
