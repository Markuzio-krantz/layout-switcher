"""Character n-gram language model with interpolated Witten-Bell smoothing.

Stage 2, step 1 of the layout switcher: a model that says how plausible a string is
as a word of one language. Pure Python, no dependencies, so every number it produces
can be traced by hand.

P(c | h) = (C(h,c) + T(h) * P(c | h[1:])) / (C(h) + T(h))
  C(h,c) = how often char c followed context h in training
  C(h)   = how often context h was seen at all
  T(h)   = how many different chars followed h (Witten-Bell: "chance of something new")
The recursion ends in a unigram with add-one smoothing over the vocabulary + 1 unknown,
so every string gets a probability > 0 and the distribution over the next char sums to 1.
"""
from __future__ import annotations

import gzip
import json
import math
from collections import Counter, defaultdict

BOS, EOS = "\x02", "\x03"  # word start / end markers


class CharLM:
    def __init__(self, order: int = 4) -> None:
        self.order = order  # n: the model looks at the previous n-1 chars
        self.counts: dict[str, Counter] = defaultdict(Counter)  # context -> next-char counts
        self._finalised = False

    # ---- training ----
    def add(self, word: str) -> None:
        s = BOS * (self.order - 1) + word + EOS
        for i in range(self.order - 1, len(s)):
            for k in range(self.order):  # contexts of length 0 .. order-1
                self.counts[s[i - k:i]][s[i]] += 1
        self._finalised = False

    def finalise(self) -> None:
        self.total = {h: sum(c.values()) for h, c in self.counts.items()}
        self.types = {h: len(c) for h, c in self.counts.items()}
        self.vocab = len(self.counts[""]) + 1  # +1: room for a char never seen
        self._finalised = True

    # ---- scoring ----
    def prob(self, ch: str, history: str) -> float:
        if not self._finalised:
            self.finalise()
        uni = self.counts[""]
        p = (uni.get(ch, 0) + 1) / (self.total.get("", 0) + self.vocab)
        for k in range(1, self.order):
            h = history[-k:] if len(history) >= k else None
            if h is None or h not in self.total:
                break
            p = (self.counts[h].get(ch, 0) + self.types[h] * p) / (self.total[h] + self.types[h])
        return p

    def logprob(self, word: str) -> float:
        """log2 P(word), including the end-of-word marker."""
        s = BOS * (self.order - 1) + word + EOS
        return sum(math.log2(self.prob(s[i], s[i - self.order + 1:i]))
                   for i in range(self.order - 1, len(s)))

    def bits_per_char(self, word: str) -> float:
        """Cross-entropy: average surprise per char. Lower = more like this language."""
        return -self.logprob(word) / (len(word) + 1)

    # ---- persistence (plain gzip JSON, no pickle: safe to load) ----
    def save(self, path: str) -> None:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            json.dump({"order": self.order, "counts": self.counts}, f, ensure_ascii=False)

    @classmethod
    def load(cls, path: str) -> "CharLM":
        with gzip.open(path, "rt", encoding="utf-8") as f:
            d = json.load(f)
        lm = cls(d["order"])
        lm.counts = defaultdict(Counter, {h: Counter(c) for h, c in d["counts"].items()})
        lm.finalise()
        return lm
