"""Measure the wrong-layout detector on held-out words.

The test data makes itself: a real word typed in the wrong layout is exactly
convert_text(word, right, wrong). So
  negatives = real held-out words as they are   (must NOT be flagged)
  positives = the same words "typed" in each other layout (must be flagged, and
              converted back to the right language)

Run:  python evaluate.py [--words 4000] [--examples]
"""
from __future__ import annotations

import argparse
import random
import sys

from convert import convert_text, script_of
from corpus import SOURCES, words
from detect import fixture_layouts, judge, load_lms

THRESHOLDS = (-1.0, -0.5, 0.0, 0.25, 0.5, 1.0, 1.5)
BASE_RATE = 0.03  # assumption: 3 words in 100 are typed in the wrong layout


def bucket(w: str) -> str:
    n = sum(1 for c in w if script_of(c))
    return "1-2" if n <= 2 else "3-4" if n <= 4 else "5+"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", type=int, default=4000, help="held-out words per language")
    ap.add_argument("--examples", action="store_true", help="print the worst errors")
    a = ap.parse_args()
    rnd = random.Random(7)
    layouts = fixture_layouts()
    by_name = {l.name: l for l in layouts}
    lms = load_lms(list(SOURCES))

    neg, pos = [], []  # (margin, bucket, word, verdict) ; pos also needs right target
    for lang in SOURCES:
        pool = list(words(lang, "test", 2_000_000))
        sample = rnd.sample(pool, min(a.words, len(pool)))
        for w in sample:
            v = judge(w, layouts, lms)
            if v is not None:
                neg.append((v, bucket(w), w))
            for wrong in layouts:
                if wrong.name == lang:
                    continue
                typed = convert_text(w, by_name[lang], wrong)
                if not any(script_of(c) == wrong.script for c in typed):
                    continue  # e.g. Hebrew "ת" -> ","  : no letters left, cannot be detected
                v = judge(typed, layouts, lms)
                if v is not None:
                    pos.append((v, bucket(w), w, lang))

    print(f"negatives (real words): {len(neg):,}   positives (wrong-layout words): {len(pos):,}\n")
    print(" thr | false alarms on real words | caught & fixed right | "
          f"precision if {BASE_RATE:.0%} are wrong")
    for thr in THRESHOLDS:
        fpr = sum(v.flagged(thr) for v, *_ in neg) / len(neg)
        rec = sum(v.flagged(thr) and v.dst == lang for v, _, _, lang in pos) / len(pos)
        prec = rec * BASE_RATE / (rec * BASE_RATE + fpr * (1 - BASE_RATE) or 1)
        print(f"{thr:+5.2f}| {fpr:26.2%} | {rec:20.2%} | {prec:8.1%}")

    print("\nby word length, threshold 0:")
    for b in ("1-2", "3-4", "5+"):
        n = [v for v, bb, _ in neg if bb == b]
        p = [(v, lang) for v, bb, _, lang in pos if bb == b]
        fpr = sum(v.flagged(0) for v in n) / max(1, len(n))
        rec = sum(v.flagged(0) and v.dst == lang for v, lang in p) / max(1, len(p))
        print(f"  {b:>4} letters: false alarms {fpr:6.2%}  caught {rec:6.2%}  (n={len(n)}/{len(p)})")

    brier(neg, pos)
    if a.examples:
        print("\nworst false alarms (real words the model wants to convert):")
        for v, _, w in sorted(neg, key=lambda x: -x[0].margin)[:15]:
            print(f"  {w:<16} -> {v.converted:<16} {v.margin:+.2f}")
        print("\nworst misses (wrong-layout words left alone):")
        for v, _, w, lang in sorted(pos, key=lambda x: x[0].margin)[:15]:
            print(f"  {v.token:<16} (meant {w}, {lang}) {v.margin:+.2f}")


def brier(neg, pos) -> None:
    """Turn the margin into a probability with logistic regression, then score that
    probability the way forecasts are scored: Brier = mean (p - outcome)^2."""
    try:
        import numpy as np
        from sklearn.linear_model import LogisticRegression
    except ImportError:
        return
    rows = [(v.margin, 0) for v, *_ in neg] + [(v.margin, 1) for v, *_ in pos]
    rows = [(m if m != float("-inf") else -20.0, y) for m, y in rows]
    random.Random(1).shuffle(rows)
    X = np.array([[m] for m, _ in rows])
    y = np.array([t for _, t in rows])
    half = len(rows) // 2
    clf = LogisticRegression().fit(X[:half], y[:half])
    p = clf.predict_proba(X[half:])[:, 1]
    base = ((y[:half].mean() - y[half:]) ** 2).mean()
    print(f"\ncalibrated probability: Brier {((p - y[half:]) ** 2).mean():.4f} "
          f"(always guessing the base rate: {base:.4f}; lower is better)")


if __name__ == "__main__":
    main()
