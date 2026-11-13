"""Content hashes and word-level diffs of extracted pages.

A page is compared by the hash of its normalized text and title, not its raw HTML. Scripts,
tokens and attribute churn do not change the hash; spacing and capitalization of the text
do not either.
"""

import difflib
import hashlib
import re
from dataclasses import dataclass

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class DiffSegment:
    op: str  # "equal", "insert" or "delete"
    text: str


@dataclass(frozen=True)
class WordDiff:
    segments: list[DiffSegment]
    words_added: int
    words_removed: int


def normalize(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip().lower()


def content_hash(title: str, text: str) -> str:
    """SHA-256 (hex) of the normalized title and text."""
    payload = f"{normalize(title)}\n{normalize(text)}".encode()
    return hashlib.sha256(payload).hexdigest()


def word_diff(old: str, new: str) -> WordDiff:
    """Word-level diff from old to new. Whitespace differences alone do not show up.

    autojunk is off: on long pages it would treat common words as noise and mark matching
    text as changed.
    """
    old_words = old.split()
    new_words = new.split()
    matcher = difflib.SequenceMatcher(None, old_words, new_words, autojunk=False)

    segments: list[DiffSegment] = []
    added = 0
    removed = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            segments.append(DiffSegment("equal", " ".join(old_words[i1:i2])))
            continue
        if i2 > i1:
            removed += i2 - i1
            segments.append(DiffSegment("delete", " ".join(old_words[i1:i2])))
        if j2 > j1:
            added += j2 - j1
            segments.append(DiffSegment("insert", " ".join(new_words[j1:j2])))
    return WordDiff(segments=segments, words_added=added, words_removed=removed)
