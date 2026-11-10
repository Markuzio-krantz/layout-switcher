"""Stage 2 unit tests: the n-gram model is a real probability distribution, and the
detector flags wrong-layout words on small hand-made corpora (no private data needed)."""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from corpus import words_in  # noqa: E402
from convert import HEBREW, LATIN  # noqa: E402
from detect import fixture_layouts, judge  # noqa: E402
from lm import EOS, CharLM  # noqa: E402

EN_WORDS = "hello world the and this that with have from there their would about which".split()
HE_WORDS = "שלום עולם של את זה הוא היא אני אתה מה לא כן גם עם על".split()
RU_WORDS = "привет мир это как что они она для все так его был".split()


def train(ws, order=3):
    lm = CharLM(order)
    for _ in range(5):
        for w in ws:
            lm.add(w)
    lm.finalise()
    return lm


class LanguageModel(unittest.TestCase):
    def test_next_char_distribution_sums_to_one(self):
        lm = train(EN_WORDS)
        seen = list(lm.counts[""])  # every char seen in training, EOS included
        self.assertIn(EOS, seen)
        for history in ("\x02\x02", "\x02h", "he", "zz"):
            # seen chars + the single "unknown char" slot ('#' was never seen)
            total = sum(lm.prob(c, history) for c in seen) + lm.prob("#", history)
            self.assertAlmostEqual(total, 1.0, places=9)

    def test_real_word_beats_shuffled(self):
        lm = train(EN_WORDS)
        self.assertLess(lm.bits_per_char("hello"), lm.bits_per_char("lhleo"))

    def test_save_load_roundtrip(self):
        import tempfile
        lm = train(HE_WORDS)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "he.json.gz")
            lm.save(p)
            self.assertAlmostEqual(CharLM.load(p).logprob("שלום"), lm.logprob("שלום"))


class Detector(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.layouts = fixture_layouts()
        cls.lms = {"en": train(EN_WORDS), "he": train(HE_WORDS), "ru": train(RU_WORDS)}

    def test_flags_hebrew_typed_in_english(self):
        v = judge("akuo", self.layouts, self.lms)
        self.assertTrue(v.flagged())
        self.assertEqual((v.dst, v.converted), ("he", "שלום"))

    def test_flags_russian_typed_in_english(self):
        v = judge("ghbdtn", self.layouts, self.lms)
        self.assertEqual((v.flagged(), v.converted), (True, "привет"))

    def test_leaves_real_english_alone(self):
        self.assertFalse(judge("hello", self.layouts, self.lms).flagged())

    def test_mixed_sentence(self):
        flagged = [t for t in "hello akuo world".split() if judge(t, self.layouts, self.lms).flagged()]
        self.assertEqual(flagged, ["akuo"])


class Corpus(unittest.TestCase):
    def test_words_filter(self):
        self.assertEqual(list(words_in("שלום, (עולם) ב-API abc", HEBREW)), ["שלום,", "עולם"])
        self.assertEqual(list(words_in("Hello, «world» x2 don't", LATIN)), ["hello,", "world", "don't"])


if __name__ == "__main__":
    unittest.main()
