"""Unit tests on a snapshot of the real layouts on the development machine (en, he Standard, ru).
Run: python -m unittest discover -s tests  (from the layout-switcher folder)."""
import json
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from convert import choose_run, convert_text, cycle_targets, detect_source  # noqa: E402
from layouts import from_json  # noqa: E402

with open(os.path.join(HERE, "fixture_layouts.json"), encoding="utf-8") as f:
    LAYOUTS = from_json(json.load(f))
L = {l.name: l for l in LAYOUTS}
EN, HE, RU = L["en"], L["he"], L["ru"]


class ConvertBothWays(unittest.TestCase):
    CASES = [  # (en, he, ru) typed on the same keys
        ("akuo", "שלום", "флгщ"),
        ("ghbdtn", "עינגאמ", "привет"),
        ("hello world", "יקךךם 'םרךג", "руддщ цщкдв"),
        ("t,ru", "אתרו", "ебкг"),
    ]

    def test_all_six_directions(self):
        for en, he, ru in self.CASES:
            texts = {"en": en, "he": he, "ru": ru}
            for a in L:
                for b in L:
                    if a != b:
                        self.assertEqual(convert_text(texts[a], L[a], L[b]), texts[b], (a, b, en))

    def test_shifted_latin_into_hebrew_gives_letters(self):
        self.assertEqual(convert_text("AKUO", EN, HE), "שלום")
        self.assertEqual(convert_text("Ghbdtn", EN, RU), "Привет")

    def test_hebrew_standard_specifics(self):
        # Standard layout: q -> '/', w -> "'", parentheses mirrored on Shift+9/0
        self.assertEqual(convert_text("qw", EN, HE), "/'")
        self.assertEqual(convert_text("()", EN, HE), ")(")
        self.assertEqual(convert_text("akuo.", EN, HE), "שלוםץ")

    def test_unmapped_chars_pass_through(self):
        self.assertEqual(convert_text("akuo 😀\n12", EN, HE), "שלום 😀\n12")

    def test_russian_punctuation_by_key(self):
        self.assertEqual(convert_text("?", EN, RU), ",")      # Shift+/ is ',' in ru
        self.assertEqual(convert_text("\"", RU, EN), "@")     # ru Shift+2


class Detection(unittest.TestCase):
    def test_detect_source(self):
        self.assertIs(detect_source("akuo", LAYOUTS), EN)
        self.assertIs(detect_source("שלום!", LAYOUTS), HE)
        self.assertIs(detect_source("привет", LAYOUTS), RU)
        self.assertIsNone(detect_source("123 ,.", LAYOUTS))

    def run_of(self, prefix):
        r = choose_run(prefix, LAYOUTS)
        return None if r is None else (prefix[r[0]:], r[1].name)

    def test_whole_line_wrong(self):
        self.assertEqual(self.run_of("akuo nv ekuo"), ("akuo nv ekuo", "en"))

    def test_trailing_foreign_run_after_correct_text(self):
        self.assertEqual(self.run_of("אני כותב akuo nv"), ("akuo nv", "en"))
        self.assertEqual(self.run_of("hello there ghbdtn"), ("hello there ghbdtn", "en"))
        self.assertEqual(self.run_of("Я пишу ש"), ("ש", "he"))

    def test_digits_and_punctuation(self):
        self.assertEqual(self.run_of("שלום 123 akuo"), ("akuo", "en"))
        self.assertEqual(self.run_of("שלום akuo 12 nv,"), ("akuo 12 nv,", "en"))
        self.assertEqual(self.run_of("שלום akuo, "), ("akuo, ", "en"))

    def test_mixed_token_cut_at_switch(self):
        self.assertEqual(self.run_of("שלוםakuo"), ("akuo", "en"))

    def test_nothing_to_convert(self):
        self.assertIsNone(self.run_of("123 ?!"))
        self.assertIsNone(self.run_of(""))


class Cycle(unittest.TestCase):
    def test_order_and_return_to_source(self):
        pref = ["he", "en", "ru"]
        self.assertEqual([l.name for l in cycle_targets(EN, LAYOUTS, pref)], ["he", "ru", "en"])
        self.assertEqual([l.name for l in cycle_targets(HE, LAYOUTS, pref)], ["en", "ru", "he"])
        self.assertEqual([l.name for l in cycle_targets(RU, LAYOUTS, pref)], ["he", "en", "ru"])


if __name__ == "__main__":
    unittest.main()
