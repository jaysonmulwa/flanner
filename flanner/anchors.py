"""Where a comment is attached, and whether it still holds.

A comment points at a quotation, not at a line or a character offset.
Offsets rot the moment anybody edits above them, and a comment that has
silently slid onto the wrong paragraph is worse than one that admits it no
longer knows where it belongs.

Resolution therefore has four outcomes, not two. "Found it" and "gone" are
the easy ones; the two in between are where the honesty lives:

``exact``     the quotation is there, at the occurrence recorded.
``moved``     it is there, but not exactly as recorded: either the
              occurrence shifted, or only a distinctive run of words from
              inside it survives. Worth showing, with less confidence.
``ambiguous`` it is there several times and the recorded position is gone,
              so we can no longer say which one was meant.
``stranded``  it is not there at all.

Only ``exact`` and ``moved`` may be shown anchored to the text. The other
two have to say so, because pretending otherwise puts somebody's words
against a sentence they never read.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .packet import render_body

EXACT = "exact"
MOVED = "moved"
AMBIGUOUS = "ambiguous"
STRANDED = "stranded"

#: How much of a quotation is worth keeping. Long enough to be unique in
#: a page of prose, short enough to survive somebody fixing a typo nearby.
MAX_QUOTE = 240

_TAGS = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Anchor:
    """A quotation, and which occurrence of it was meant."""

    quote: str
    occurrence: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"quote": self.quote, "occurrence": self.occurrence}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Anchor:
        """Tolerant on the way in: this shape arrives from stored JSON and
        from a review packet written by somebody else's browser."""
        try:
            where = int(raw.get("occurrence") or 0)
        except (TypeError, ValueError):
            where = 0
        return cls(quote=str(raw.get("quote") or ""), occurrence=max(0, where))


@dataclass(frozen=True)
class Resolution:
    """Whether an anchor still finds its text, and what to highlight."""

    status: str
    occurrence: int = -1
    #: The text that actually matched. The whole quotation when it survived
    #: intact, a fragment of it when only part did. What the UI should mark.
    matched: str = ""

    @property
    def anchored(self) -> bool:
        """True when the comment may be shown against the text itself."""
        return self.status in (EXACT, MOVED)


def plain_text(body: str) -> str:
    """The words a reader sees, with the markup taken out.

    Rendered first rather than stripped with a regex over the source: a
    reader selects `session_store.py`, not `` `session_store.py` ``, and an
    anchor has to match what they actually saw.
    """
    text = _TAGS.sub("", render_body(body))
    # Entities that survive tag-stripping, in the order that matters: an
    # unescaped ampersand first would corrupt the rest.
    for entity, char in (
        ("&lt;", "<"),
        ("&gt;", ">"),
        ("&quot;", '"'),
        ("&#39;", "'"),
        ("&nbsp;", " "),
        ("&amp;", "&"),
    ):
        text = text.replace(entity, char)
    return text


def normalise(text: str) -> str:
    """Collapse whitespace, so a rewrapped paragraph still matches.

    Reflowing a plan to a different column width changes every newline in
    it without changing a word. Anchoring that could not survive that would
    be broken by a formatter.
    """
    return _SPACE.sub(" ", text).strip()


def occurrences(quote: str, body: str) -> int:
    """How many times a quotation appears in a plan."""
    needle = normalise(quote)
    if not needle:
        return 0
    return normalise(plain_text(body)).count(needle)


#: The shortest run of words worth trusting as a fallback match, and the
#: shortest such run in characters. Six words of prose that appear exactly
#: once in a plan is good evidence; three words is a coincidence waiting to
#: happen. Both thresholds were picked by measuring real revisions.
MIN_RUN_WORDS = 6
MIN_RUN_CHARS = 25


def _distinctive_run(needle: str, hay: str) -> str:
    """The longest run of words from the quotation that appears exactly once.

    When somebody edits a sentence a comment is attached to, the whole
    quotation stops matching but most of it survives. Measured against this
    repo's own history, this recovers about a third of the anchors that
    would otherwise be declared lost.

    Exactly once, never merely "at least once": a fragment appearing twice
    tells us no more than the failed quotation did.
    """
    words = needle.split()
    for size in range(len(words) - 1, MIN_RUN_WORDS - 1, -1):
        for start in range(len(words) - size + 1):
            run = " ".join(words[start : start + size])
            if len(run) >= MIN_RUN_CHARS and hay.count(run) == 1:
                return run
    return ""


def resolve(anchor: Anchor, body: str) -> Resolution:
    """Whether this anchor still finds its text in a given version."""
    needle = normalise(anchor.quote)
    if not needle:
        return Resolution(STRANDED)

    hay = normalise(plain_text(body))
    found = hay.count(needle)
    if found > anchor.occurrence:
        # The recorded position still exists. Whether text above it moved is
        # not something we can tell, and does not matter: the quotation at
        # that index is the one that was meant.
        return Resolution(EXACT, anchor.occurrence, needle)
    if found == 1:
        # Occurrences were removed, but exactly one is left. There is no
        # ambiguity about which, so the comment travels with it.
        return Resolution(MOVED, 0, needle)
    if found > 1:
        return Resolution(AMBIGUOUS)

    run = _distinctive_run(needle, hay)
    if run:
        return Resolution(MOVED, 0, run)
    return Resolution(STRANDED)


def clip(quote: str) -> str:
    """A quotation trimmed to what is worth storing."""
    tidy = normalise(quote)
    return tidy if len(tidy) <= MAX_QUOTE else tidy[: MAX_QUOTE - 1].rstrip() + "…"
