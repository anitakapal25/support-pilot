"""Explicit operator provisioning; no public registration or cloud demo-password seeding."""

import argparse
from getpass import getpass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import password_hasher
from app.config import Settings
from app.database import make_engine
from app.models import User
from app.schemas import LoginRequest
from app.seed import DEMO_PASSWORD


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--role", choices=["customer", "support"], required=True)
    args = parser.parse_args()
    password = getpass("New password (not echoed): ")
    if len(password) < 16 or password == DEMO_PASSWORD or password != getpass("Confirm password: "):
        raise SystemExit("Use a matching, unique password of at least 16 characters")
    validated = LoginRequest(email=args.email, password=password)
    if not 1 <= len(args.name) <= 100:
        raise SystemExit("Name must be 1..100 characters")
    engine = make_engine(Settings())
    try:
        with Session(engine) as db, db.begin():
            if db.scalar(select(User.id).where(User.email == validated.email)):
                raise SystemExit("Account already exists; no changes made")
            db.add(
                User(
                    email=validated.email,
                    name=args.name,
                    role=args.role,
                    password_hash=password_hasher.hash(password),
                )
            )
        print("Account created")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
