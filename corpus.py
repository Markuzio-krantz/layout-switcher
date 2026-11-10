"""Local training text for the language models: words of one alphabet, split into
train / held-out by FILE (not by word), so the test words come from documents the
model never saw. Nothing leaves the machine; the trained models live in
%LOCALAPPDATA%\\layout-switcher\\lm (macOS ~/Library/Application Support/layout-switcher/lm,
Linux ~/.local/state/layout-switcher/lm), outside the repo, because their counts are
statistics of Mark's private notes.
"""
from __future__ import annotations

import glob
import hashlib
import os
import sys
from collections.abc import Iterator

from convert import CYRILLIC, HEBREW, LATIN, script_of

HOME = os.path.expanduser("~")
VAULT = os.path.join(HOME, "Documents", "MyVault", "MarkSpaceIt")
BOOKS = os.path.join(HOME, "Documents", "STUDY", "BOOKS", "_text")
if os.name == "nt":
    MODEL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", HOME), "layout-switcher", "lm")
elif sys.platform == "darwin":
    MODEL_DIR = os.path.join(HOME, "Library", "Application Support", "layout-switcher", "lm")
else:  # next to the log, outside the install folder, so an uninstall keeps the models
    MODEL_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state"),
                             "layout-switcher", "lm")

# language -> (script, list of (root, glob pattern))
SOURCES = {
    "he": (HEBREW, [(VAULT, "**/*.md")]),
    "en": (LATIN, [(BOOKS, "**/*.txt")]),
    "ru": (CYRILLIC, [(BOOKS, "**/*.txt"), (VAULT, "**/*.md")]),
}
SKIP_DIRS = (".git", "node_modules", ".claude", "archive")
STRIP = "\"“”„«»()[]{}*_`<>"  # wrappers removed from both ends; , . ; : ' stay (they are keys)
ALLOWED = set(",.;:'!?-")      # non-letters a word may still contain
HELD_OUT_SHARE = 10            # 1 file in 10 goes to the test set


def is_test_file(path: str) -> bool:
    return int(hashlib.md5(path.encode("utf-8")).hexdigest(), 16) % HELD_OUT_SHARE == 0


def words_in(text: str, script: str) -> Iterator[str]:
    for raw in text.split():
        w = raw.strip(STRIP).lower()
        has_letter = False
        for ch in w:
            s = script_of(ch)
            if s == script:
                has_letter = True
            elif s is not None or ch not in ALLOWED:
                break
        else:
            if has_letter:
                yield w


def files(lang: str) -> Iterator[str]:
    for root, pattern in SOURCES[lang][1]:
        for p in glob.iglob(os.path.join(root, pattern), recursive=True):
            if not any(f"{os.sep}{d}{os.sep}" in p for d in SKIP_DIRS):
                yield p


def words(lang: str, split: str, max_chars: int) -> Iterator[str]:
    """Yield words of `lang` from the train or test split until max_chars is reached."""
    script = SOURCES[lang][0]
    want_test = split == "test"
    n = 0
    for p in files(lang):
        if is_test_file(p) != want_test:
            continue
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except OSError:
            continue
        for w in words_in(text, script):
            yield w
            n += len(w)
            if n >= max_chars:
                return
