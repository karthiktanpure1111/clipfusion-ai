"""Create a ClipFusion user from the command line (e.g. the first admin).

Usage::

    cd backend
    .venv/Scripts/python.exe scripts/create_user.py --email you@example.com --name "Your Name" [--admin]
    # password: --password, the CLIPFUSION_PASSWORD env var, or an interactive prompt

The password must satisfy the same strength rules as /auth/register.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a ClipFusion user")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", default="")
    parser.add_argument("--password", default=None, help="prefer CLIPFUSION_PASSWORD or the prompt")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--admin", action="store_true", help="give the user the 'admin' role")
    args = parser.parse_args(argv)

    from sqlalchemy import func, select

    from app.core.database import SessionLocal
    from app.core.errors import AppError
    from app.core.security import hash_password, validate_password_strength
    from app.models import User
    from app.modules.users.service import validate_timezone

    password = args.password or os.environ.get("CLIPFUSION_PASSWORD")
    if not password:
        password = getpass.getpass("Password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match", file=sys.stderr)
            return 2
    email = args.email.strip().lower()
    try:
        validate_password_strength(password)
        tz = validate_timezone(args.timezone)
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 2
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(func.lower(User.email) == email)) is not None:
            print(f"Error: a user with email {email} already exists", file=sys.stderr)
            return 1
        user = User(email=email, password_hash=hash_password(password), full_name=args.name.strip() or email,
                    timezone=tz, role="admin" if args.admin else "user", settings={})
        db.add(user)
        db.commit()
        print(f"Created user {user.id} ({email}, role={user.role})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
