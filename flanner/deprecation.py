"""Telling people something is going away, before it goes.

There was no mechanism for this, and it showed: `doctor --output json`
changed from a bare array to an object in 0.9.2 with no warning and no
transition. Anything parsing it broke on upgrade, and the first a caller
knew was a KeyError.

The rule this exists to enforce: a thing that is going away announces itself
for at least one minor version before it goes, and the announcement names
what to use instead. A warning that says "deprecated" and nothing else moves
the work of finding the replacement onto the person least equipped to do it.

Warnings go to stderr, so they never corrupt `--output json` on stdout. A
deprecation notice that breaks the machine-readable output is worse than the
deprecation.
"""

from __future__ import annotations

import warnings


def warn(what: str, *, instead: str, removed_in: str, since: str) -> None:
    """Announce that something is going away.

    Args:
        what: the thing being deprecated, as somebody would refer to it —
            a flag, a command, an output shape.
        instead: what to use now. Required, not optional: a deprecation
            without a replacement is a removal with extra steps.
        removed_in: the version it stops working. Naming it turns "some day"
            into a date somebody can plan around.
        since: the version that started warning, so a reader can tell how
            long they have had.

    Raises:
        ValueError: if `instead` is empty. Enforced rather than trusted,
            because the whole value of the message is the replacement.
    """
    if not instead.strip():
        raise ValueError(f"deprecating {what!r} needs a replacement to point at")

    warnings.warn(
        f"{what} is deprecated since {since} and stops working in {removed_in}. Use {instead}.",
        DeprecationWarning,
        stacklevel=3,
    )
