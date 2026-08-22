"""Whether a comment still knows where it belongs.

The four outcomes are the whole point of this module, so each one gets a
test that makes it happen for a real reason rather than by construction.
"""

import pytest

from flanner.anchors import (
    AMBIGUOUS,
    EXACT,
    MAX_QUOTE,
    MOVED,
    STRANDED,
    Anchor,
    clip,
    normalise,
    occurrences,
    plain_text,
    resolve,
)

BODY = """# Retry policy

The retry budget is three attempts, with exponential backoff.

Rate limits are counted per tenant, not per key.
"""


def at(quote, occurrence=0):
    return Anchor(quote=quote, occurrence=occurrence)


# --- the four outcomes ---------------------------------------------------


def test_an_untouched_quotation_resolves_exactly():
    got = resolve(at("The retry budget is three attempts"), BODY)
    assert got.status == EXACT
    assert got.anchored
    assert got.matched == "The retry budget is three attempts"


def test_a_quotation_still_present_once_after_a_sibling_was_deleted_moves():
    body = "alpha. The same line. beta. The same line. gamma."
    # Written against the second occurrence, which is then removed.
    anchor = at("The same line", occurrence=1)
    assert resolve(anchor, body).status == EXACT
    got = resolve(anchor, "alpha. The same line. gamma.")
    assert got.status == MOVED
    assert got.anchored
    assert got.occurrence == 0


def test_a_partly_rewritten_sentence_keeps_the_comment_on_what_survived():
    quote = (
        "The retry budget is three attempts, with exponential backoff "
        "and a ceiling of thirty seconds between tries"
    )
    edited = f"# Retry policy\n\n{quote}.\n".replace("three attempts", "five attempts")
    got = resolve(at(quote), edited)
    assert got.status == MOVED
    # Only the surviving run is offered for highlighting, never the whole
    # quotation: part of it is no longer on the page.
    # Everything from the first surviving word onward, not merely the tail
    # after the edited clause: only "three" changed, so "attempts," is still
    # there and is part of what the reader can be shown.
    assert got.matched == (
        "attempts, with exponential backoff and a ceiling of thirty seconds between tries"
    )
    assert got.matched in normalise(plain_text(edited))


def test_a_survivor_too_short_to_be_evidence_strands_instead_of_guessing():
    """Four words that happen to still be there is not enough to move
    somebody's comment onto a sentence they did not write it against."""
    quote = "The retry budget is three attempts, with exponential backoff"
    edited = f"{quote}.\n".replace("The retry budget is three attempts", "Retries are queued")
    assert resolve(at(quote), edited).status == STRANDED


def test_a_duplicated_quotation_whose_position_is_gone_is_ambiguous():
    anchor = at("see below", occurrence=2)
    got = resolve(anchor, "see below. and again see below. done.")
    assert got.status == AMBIGUOUS
    assert not got.anchored
    # Nothing is offered to highlight, because we cannot say which one.
    assert got.matched == ""


def test_a_rewritten_paragraph_strands_the_comment():
    rewritten = "# Retry policy\n\nRetries are handled by the queue now.\n"
    got = resolve(at("The retry budget is three attempts"), rewritten)
    assert got.status == STRANDED
    assert not got.anchored


def test_an_empty_quotation_is_stranded_rather_than_matching_everything():
    for empty in ("", "   ", "\n"):
        assert resolve(at(empty), BODY).status == STRANDED


# --- what must not count as a match --------------------------------------


def test_a_short_surviving_fragment_is_not_enough_to_claim_a_match():
    """Three common words appearing once is a coincidence, not evidence."""
    got = resolve(at("the queue is drained by the worker on a timer"), "the queue is gone")
    assert got.status == STRANDED


def test_a_fragment_that_survives_twice_is_no_better_than_none():
    body = "we retry the failed request twice. later we retry the failed request twice."
    got = resolve(at("we retry the failed request twice under load"), body)
    assert got.status == STRANDED


# --- what must survive ---------------------------------------------------


def test_reflowing_a_paragraph_does_not_break_an_anchor():
    wrapped = BODY.replace(
        "The retry budget is three attempts, with exponential backoff.",
        "The retry budget is three\nattempts, with exponential\nbackoff.",
    )
    assert resolve(at("The retry budget is three attempts"), wrapped).status == EXACT


def test_an_anchor_matches_what_a_reader_saw_not_the_markdown_source():
    """A reader selects `session.py`, not the backticks around it."""
    body = "Config lives in `session.py` and nowhere else.\n"
    assert resolve(at("Config lives in session.py"), body).status == EXACT


@pytest.mark.parametrize(
    "source,shown",
    [("a **bold** claim", "a bold claim"), ("5 < 6 & 7 > 6", "5 < 6 & 7 > 6")],
)
def test_entities_and_markup_are_resolved_before_matching(source, shown):
    assert shown in normalise(plain_text(source))


# --- storage shape -------------------------------------------------------


def test_an_anchor_survives_a_round_trip_through_json():
    original = at("something quoted", occurrence=3)
    assert Anchor.from_dict(original.to_dict()) == original


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"quote": None},
        {"quote": "x", "occurrence": None},
        {"quote": "x", "occurrence": "not a number"},
        {"quote": "x", "occurrence": -4},
    ],
)
def test_a_malformed_anchor_from_outside_is_taken_in_rather_than_crashing(raw):
    """This shape arrives from a stranger's browser, so it cannot be trusted
    to be well formed, and a review packet must never be able to raise."""
    anchor = Anchor.from_dict(raw)
    assert anchor.occurrence >= 0
    assert isinstance(anchor.quote, str)


def test_a_long_quotation_is_clipped_and_says_so():
    clipped = clip("word " * 200)
    assert len(clipped) <= MAX_QUOTE
    assert clipped.endswith("…")


def test_clipping_leaves_a_normal_quotation_alone_apart_from_whitespace():
    assert clip("  two   words  ") == "two words"


def test_counting_occurrences_uses_the_same_view_as_matching():
    """The count that a UI shows and the index that resolution uses have to
    come from one place, or a note lands on the wrong paragraph."""
    body = "`x.py` here. And `x.py` there.\n"
    assert occurrences("x.py", body) == 2
    assert resolve(at("x.py", occurrence=1), body).status == EXACT
    assert resolve(at("x.py", occurrence=2), body).status == AMBIGUOUS
