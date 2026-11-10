"""Train one character n-gram model per language from local text and report how well
each model predicts each language's held-out text (bits per character).

Run:  python train_lm.py [--order 4] [--chars 5000000]
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from corpus import MODEL_DIR, SOURCES, words
from lm import CharLM


def model_path(lang: str) -> str:
    return os.path.join(MODEL_DIR, f"{lang}.json.gz")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", type=int, default=4)
    ap.add_argument("--chars", type=int, default=5_000_000, help="training chars per language")
    ap.add_argument("--test-chars", type=int, default=300_000)
    a = ap.parse_args()
    os.makedirs(MODEL_DIR, exist_ok=True)

    lms = {}
    for lang in SOURCES:
        t = time.perf_counter()
        lm = CharLM(a.order)
        n_words = n_chars = 0
        for w in words(lang, "train", a.chars):
            lm.add(w)
            n_words += 1
            n_chars += len(w)
        lm.finalise()
        lm.save(model_path(lang))
        size = os.path.getsize(model_path(lang)) / 1e6
        print(f"{lang}: {n_words:,} words, {n_chars:,} chars, {len(lm.counts):,} contexts, "
              f"vocab {lm.vocab}, file {size:.1f} MB, {time.perf_counter() - t:.0f}s")
        lms[lang] = lm

    print("\nbits per char on held-out text (rows = model, columns = text). "
          "Diagonal should be lowest:")
    tests = {lang: list(words(lang, "test", a.test_chars)) for lang in SOURCES}
    print("model\\text " + "".join(f"{t:>8}" for t in tests))
    for m, lm in lms.items():
        row = []
        for t, ws in tests.items():
            bits = sum(-lm.logprob(w) for w in ws) / max(1, sum(len(w) + 1 for w in ws))
            row.append(f"{bits:8.2f}")
        print(f"{m:>10} " + "".join(row))


if __name__ == "__main__":
    main()
