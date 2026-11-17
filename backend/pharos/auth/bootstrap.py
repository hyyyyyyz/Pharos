"""Creating the first administrator.

``/api/admin`` lets an existing admin promote others, so an instance needs one
way in that does not go through the API. Two are offered, both operator-only:

* ``PHAROS_ADMIN_EMAILS`` — applied at startup to accounts that already exist.
* ``python -m pharos.cli promote EMAIL`` — run on the host, against the database.

Both require control of the server's environment or shell, which is exactly the
trust boundary that already decides who holds the auth secret and the API keys.
Neither is reachable over HTTP, and neither grants admin at *registration*:
the first-registered account is not made admin implicitly, because on an
instance with open sign-up that would hand the role to whoever arrives first.
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from pharos.db.models import User
from pharos.db.session import session_scope

log = logging.getLogger(__name__)


def promote_by_email(email: str) -> bool:
    """Make the account with this address an active administrator.

    Returns False when no such account exists. Idempotent.
    """
    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == email.strip().casefold()))
        if user is None:
            return False
        user.is_admin = True
        user.is_active = True
        return True


def apply_configured_admins(emails: list[str]) -> int:
    """Promote configured addresses that have registered; returns how many changed."""
    changed = 0
    for email in emails:
        with session_scope() as s:
            user = s.scalar(select(User).where(User.email == email))
            if user is None:
                log.warning("PHAROS_ADMIN_EMAILS lists an address with no account yet; "
                            "register it, then restart")
                continue
            if not user.is_admin:
                user.is_admin = True
                changed += 1
    if changed:
        log.warning("promoted %d account(s) to administrator from PHAROS_ADMIN_EMAILS", changed)
    return changed
