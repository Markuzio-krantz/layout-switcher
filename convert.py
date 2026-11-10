"""Pure layout-conversion logic: no Windows calls, so it is unit-testable.

A Layout maps physical keys (scancode, shift) to the character that layout
types. Converting text = for each char, find the key that produced it in the
source layout, then emit what the target layout puts on that same key.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

HEBREW, CYRILLIC, LATIN = "hebrew", "cyrillic", "latin"
CASELESS = {HEBREW}
WHITESPACE = set(" \t\r\n ")

Key = tuple[int, bool]  # (scancode, shift)


def script_of(ch: str) -> str | None:
    o = ord(ch)
    if 0x05D0 <= o <= 0x05EA or 0x05F0 <= o <= 0x05F2:
        return HEBREW
    if 0x0400 <= o <= 0x04FF:
        return CYRILLIC
    if ch.isascii() and ch.isalpha():
        return LATIN
    return None


@dataclass
class Layout:
    name: str
    hkl: int
    keys: dict[Key, str]
    rev: dict[str, Key] = field(init=False)
    script: str | None = field(init=False)

    def __post_init__(self) -> None:
        self.rev = {}
        # Unshifted keys first, so a char found on both levels maps to the plain key.
        for want_shift in (False, True):
            for (sc, sh), ch in sorted(self.keys.items()):
                if sh == want_shift and len(ch) == 1 and ch not in self.rev:
                    self.rev[ch] = (sc, sh)
        counts = Counter(
            s for (sc, sh), ch in self.keys.items()
            if not sh and len(ch) == 1 and (s := script_of(ch))
        )
        self.script = counts.most_common(1)[0][0] if counts else None


def convert_text(text: str, src: Layout, dst: Layout) -> str:
    out = []
    for ch in text:
        key = src.rev.get(ch)
        if key is None:
            out.append(ch)
            continue
        new = dst.keys.get(key, "")
        # Shift in a caseless layout (Hebrew Standard) types Latin capitals; a user who
        # had Shift/CapsLock on in the wrong layout still meant the Hebrew letter.
        if key[1] and dst.script in CASELESS and script_of(new or " ") == LATIN:
            new = dst.keys.get((key[0], False), new)
        out.append(new if len(new) == 1 else ch)
    return "".join(out)


def _layout_for_script(script: str, layouts: list[Layout]) -> Layout | None:
    return next((l for l in layouts if l.script == script), None)


def detect_source(text: str, layouts: list[Layout]) -> Layout | None:
    """The layout whose alphabet dominates the text's letters."""
    counts = Counter(s for ch in text if (s := script_of(ch)))
    for script, _ in counts.most_common():
        lay = _layout_for_script(script, layouts)
        if lay:
            return lay
    return None


def choose_run(prefix: str, layouts: list[Layout]) -> tuple[int, Layout] | None:
    """For text from line start to caret, find the trailing run typed in one alphabet.

    Returns (start index of the run in prefix, its source layout), or None.
    Walks back from the caret over whitespace-separated tokens while their letters are
    in the same alphabet as the last letter; a token that switches alphabet midway is
    cut at the switch. Letterless tokens (digits, punctuation) join the run only if a
    same-alphabet token lies before them.
    """
    last = next((script_of(c) for c in reversed(prefix) if script_of(c)), None)
    if last is None:
        return None
    src = _layout_for_script(last, layouts)
    if src is None:
        return None
    # token spans
    spans, i, n = [], 0, len(prefix)
    while i < n:
        if prefix[i] in WHITESPACE:
            i += 1
            continue
        j = i
        while j < n and prefix[j] not in WHITESPACE:
            j += 1
        spans.append((i, j))
        i = j
    start = None
    seen_letters = False
    for a, b in reversed(spans):
        scripts = [script_of(c) for c in prefix[a:b]]
        if not any(scripts):
            if not seen_letters:  # trailing digits/punctuation after the last word
                start = a
            continue  # between words: joins only if a same-alphabet word precedes it
        if all(s in (None, last) for s in scripts):
            start = a
            seen_letters = True
            continue
        # mixed token: keep the suffix after the last foreign letter
        cut = max(k for k, s in enumerate(scripts) if s not in (None, last)) + 1
        if any(s == last for s in scripts[cut:]):
            start = a + cut
        break
    if start is None:
        return None
    return start, src


def cycle_targets(src: Layout, layouts: list[Layout], preference: list[str]) -> list[Layout]:
    """Order of targets for repeated presses: preferred languages first, then the rest,
    ending back at the source so a final press restores the original text."""
    by_name = {l.name: l for l in layouts}
    order = [by_name[p] for p in preference if p in by_name]
    order += [l for l in layouts if l not in order]
    return [l for l in order if l is not src] + [src]
