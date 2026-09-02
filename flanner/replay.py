"""Refusing a peer request that has already been answered (PRD §21.1).

The control plane has spent nonces since revocation shipped. Between two
devices, nothing did — `device_auth` bounded how *old* a signed request may
be, and that was the whole defence. A window is a poor sole defence, because
every field including the signature travels to anyone who can see the
connection, and a captured request stays perfectly valid until it expires.

It also made the window impossible to widen. Clocks drift, and on Windows
the time service ships stopped, so two machines minutes apart could not sync
at all. The only knob was a security tradeoff: every second of tolerance for
drifting clocks was a second of tolerance for replay.

Spending the nonce separates those two questions. Freshness bounds how long
a nonce must be remembered; uniqueness is what actually stops the replay. So
the window can be sized for clocks and this can be sized for memory.

**Recorded only after the signature verifies.** Otherwise any caller could
fill this table, or burn a nonce belonging to a request they had not
authored.

**Insert first, ask later.** Checking for a row and then writing one leaves
a gap two concurrent copies of the same request can both pass through. The
unique constraint settles it atomically instead, so exactly one wins however
they interleave.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.exc import IntegrityError

from .database import SeenNonceModel
from .device_auth import MAX_SKEW


def _utcnow() -> datetime:
    """Naive UTC, matching the DateTime columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# How long a spent nonce is remembered. Past this, device_auth refuses the
# request on age anyway, so forgetting it reopens nothing: the two rules
# cover the same window from opposite ends. Tied to MAX_SKEW rather than
# written out, so widening the window cannot silently shorten this.
RETENTION = MAX_SKEW + timedelta(seconds=30)


def remember(session: Any, *, device_id: str, nonce: str, now: datetime | None = None) -> bool:
    """Claim a nonce for this device. False if it was already spent.

    Commits on its own, because a nonce has to stay spent even when the
    request that used it goes on to fail. Leaving it to the caller's commit
    would make every error path a replay window.
    """
    moment = now or _utcnow()
    prune(session, moment)

    session.add(SeenNonceModel(device_id=device_id, nonce=nonce, expires_at=moment + RETENTION))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return True


def prune(session: Any, moment: datetime | None = None) -> None:
    """Drop nonces too old to be replayed anyway.

    Cheap because the window is minutes: this table holds the requests of the
    last few minutes, not a history. Called on the way past rather than on a
    timer, so a device that never serves never accumulates anything.
    """
    session.query(SeenNonceModel).filter(
        SeenNonceModel.expires_at < (moment or _utcnow())
    ).delete()
