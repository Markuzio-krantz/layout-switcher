"""Local training text for the language models: words of one alphabet, split into
train / held-out by FILE (not by word), so the test words come from documents the
model never saw. Nothing leaves the machine; the trained models live in
%LOCALAPPDATA%\\layout-switcher\\lm (macOS ~/Library/Application Support/layout-switcher/lm,
Linux ~/.local/state/layout-switcher/lm), outside the repo, because their counts are
statistics of the user's own private texts.

Which folders to learn from is set per machine in corpus.json next to the models'
folder (never in the repo), e.g.
  {"he": ["C:/Users/me/Notes"], "en": ["C:/Users/me/Books"], "ru": ["C:/Users/me/Books"]}
Each folder is searched for *.md and *.txt. Without the file, every language reads
the Documents folder.
"""
from __future__ import annotations

import glob
import hashlib
import json
import os
import sys
from collections.abc import Iterator

from convert import CYRILLIC, HEBREW, LATIN, script_of

HOME = os.path.expanduser("~")
if os.name == "nt":
    MODEL_DIR = os.path.join(os.environ.get("LOCALAPPDATA", HOME), "layout-switcher", "lm")
elif sys.platform == "darwin":
    MODEL_DIR = os.path.join(HOME, "Library", "Application Support", "layout-switcher", "lm")
else:  # next to the log, outside the install folder, so an uninstall keeps the models
    MODEL_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.join(HOME, ".local", "state"),
                             "layout-switcher", "lm")

CORPUS_CONFIG = os.path.join(os.path.dirname(MODEL_DIR), "corpus.json")
SCRIPTS = {"he": HEBREW, "en": LATIN, "ru": CYRILLIC}


def _load_sources() -> dict[str, tuple[str, list[tuple[str, str]]]]:
    """language -> (script, list of (root, glob pattern))"""
    try:
        with open(CORPUS_CONFIG, encoding="utf-8") as f:
            dirs = json.load(f)
    except (OSError, ValueError):
        dirs = {}
    out = {}
    for lang, script in SCRIPTS.items():
        roots = dirs.get(lang) or [os.path.join(HOME, "Documents")]
        out[lang] = (script, [(r, p) for r in roots for p in ("**/*.md", "**/*.txt")])
    return out


SOURCES = _load_sources()
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
