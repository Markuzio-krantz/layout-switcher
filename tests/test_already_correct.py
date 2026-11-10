"""looks_already_correct: skip selections that are already correct text.

Needs the trained models in %LOCALAPPDATA%\\layout-switcher\\lm (built from private
notes, not in the repo); skipped where they don't exist."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from detect import fixture_layouts, load_lms, looks_already_correct  # noqa: E402

LAYOUTS = fixture_layouts()
LMS = load_lms([l.name for l in LAYOUTS])


class NoModels(unittest.TestCase):
    def test_without_models_everything_converts(self):
        self.assertFalse(looks_already_correct("שלום עולם", LAYOUTS, {}))


@unittest.skipUnless(len(LMS) == 3, "trained language models not present")
class WithModels(unittest.TestCase):
    def test_correct_text_is_left_alone(self):
        # the real case: a quote highlighted in the reply box of the Claude app
        for text in ["מה זה לא עושה: VPN מסתיר את הכתובת, אבל לא הופך הורדה לא חוקית לחוקית",
                     "hello world", "Привет как дела"]:
            self.assertTrue(looks_already_correct(text, LAYOUTS, LMS), text)

    def test_wrong_layout_text_still_converts(self):
        for text in ["akuo nv", "гея снпгб", "עינגאמ", "akuo"]:
            self.assertFalse(looks_already_correct(text, LAYOUTS, LMS), text)

    def test_digits_and_punctuation_only_converts(self):
        self.assertFalse(looks_already_correct("123 ,.", LAYOUTS, LMS))


if __name__ == "__main__":
    unittest.main()
