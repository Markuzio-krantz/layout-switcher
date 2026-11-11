"""Which words were typed in the wrong layout?

For each word: score it as-is in the language of its own alphabet, then convert it to
every other layout and score the result in that language. The margin is how many bits
per char more plausible the best conversion is. Margin > threshold -> flag the word.

Run:  python detect.py "hello akuo"
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass

from convert import Layout, convert_text, detect_source, script_of
from lm import CharLM

HERE = os.path.dirname(os.path.abspath(__file__))
# bits per char. evaluate.py, 2026-11-10: at 1.0 -> 0.30% false alarms, 98.4% caught;
# at 0.0 -> 0.72% / 99.3%. Chosen 1.0: a wrong conversion of a real word costs more
# than a missed one (the hotkey still converts it by hand).
THRESHOLD = 1.0


@dataclass
class Verdict:
    token: str
    src: str
    dst: str | None
    converted: str | None
    margin: float  # bits/char: >0 means the conversion looks more like a real word

    def flagged(self, threshold: float = THRESHOLD) -> bool:
        return self.dst is not None and self.margin > threshold


def judge(token: str, layouts: list[Layout], lms: dict[str, CharLM]) -> Verdict | None:
    src = detect_source(token, layouts)
    if src is None or src.name not in lms:
        return None
    t = token.lower()
    base = lms[src.name].logprob(t)
    best = Verdict(token, src.name, None, None, float("-inf"))
    for dst in layouts:
        if dst is src or dst.name not in lms:
            continue
        conv = convert_text(t, src, dst)
        scripts = {script_of(c) for c in conv} - {None}
        if scripts != {dst.script}:  # leftovers from another alphabet: not a clean conversion
            continue
        margin = (lms[dst.name].logprob(conv) - base) / (len(t) + 1)
        if margin > best.margin:
            best = Verdict(token, src.name, dst.name, conv, margin)
    return best


def looks_already_correct(text: str, layouts: list[Layout], lms: dict[str, CharLM],
                          threshold: float = THRESHOLD) -> bool:
    """True when most words of `text` read as real words of their own language.

    Used on a selection before converting it: some apps (the Claude desktop reply box)
    answer Ctrl+C with a selection elsewhere on the page, so correct text arrives as if
    the user had selected it. Fewer than half of the words flagged -> leave it alone.
    No models, or no judgeable words -> False (convert as before)."""
    if not lms:
        return False
    verdicts = [judge(t, layouts, lms) for t in text.split()]
    judged = [v for v in verdicts if v is not None and v.dst is not None]
    if not judged:
        return False
    return sum(v.flagged(threshold) for v in judged) * 2 < len(judged)


def suspect_span(text: str, layouts: list[Layout], lms: dict[str, CharLM],
                 real_margin: float = -THRESHOLD) -> tuple[int, int] | None:
    """Char span from the first to the last word that is NOT confidently real.

    A word is confidently real when no conversion of it beats it, or the best one loses
    by more than |real_margin| bits/char. Letterless tokens never decide. Returns None
    when every word looks real (the caller then converts everything, as in stage 1).
    """
    first = last = None
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            i += 1
            continue
        j = i
        while j < n and not text[j].isspace():
            j += 1
        v = judge(text[i:j], layouts, lms)
        if v is not None and v.dst is not None and v.margin >= real_margin:
            first = i if first is None else first
            last = j
        i = j
    return None if first is None else (first, last)


def best_target(text: str, layouts: list[Layout], lms: dict[str, CharLM]) -> str | None:
    """The language the flagged words of `text` most likely belong to (margins summed
    per language), or None when no word is flagged."""
    votes: dict[str, float] = {}
    for tok in text.split():
        v = judge(tok, layouts, lms)
        if v is not None and v.flagged():
            votes[v.dst] = votes.get(v.dst, 0.0) + v.margin
    return max(votes, key=votes.get) if votes else None


def load_lms(names: list[str]) -> dict[str, CharLM]:
    from train_lm import model_path
    return {n: CharLM.load(model_path(n)) for n in names if os.path.exists(model_path(n))}


def fixture_layouts() -> list[Layout]:
    from layouts import from_json
    with open(os.path.join(HERE, "tests", "fixture_layouts.json"), encoding="utf-8") as f:
        return from_json(json.load(f))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    layouts = fixture_layouts()
    lms = load_lms([l.name for l in layouts])
    if not lms:
        sys.exit("No models yet. Run: python train_lm.py")
    for tok in " ".join(sys.argv[1:]).split():
        v = judge(tok, layouts, lms)
        if v is None or v.dst is None:
            print(f"{tok:>16}  -")
            continue
        mark = "FLAG ->" if v.flagged() else "ok     "
        print(f"{tok:>16}  {mark} {v.converted:<16} {v.src}->{v.dst}  margin {v.margin:+.2f} bits/char")
