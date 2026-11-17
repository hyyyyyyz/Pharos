"""Operator commands, run on the server host.

    python -m pharos.cli promote you@example.org   # make an existing account admin
    python -m pharos.cli admins                    # list administrators
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from pharos.auth.bootstrap import promote_by_email
from pharos.config import get_settings
from pharos.db.models import User
from pharos.db.session import init_engine, session_scope


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="pharos.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("promote", help="make an existing account an administrator")
    p.add_argument("email")
    sub.add_parser("admins", help="list administrator accounts")
    args = parser.parse_args(argv)

    init_engine(get_settings().db_path)
    if args.command == "promote":
        if not promote_by_email(args.email):
            print(f"No account with email {args.email!r}. Register it first.", file=sys.stderr)
            return 1
        print(f"{args.email.strip().casefold()} is now an administrator.")
        return 0
    with session_scope() as s:
        for email in s.scalars(select(User.email).where(User.is_admin.is_(True))):
            print(email)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
