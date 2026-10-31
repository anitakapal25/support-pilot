"""Run explicitly with `python -m app.seed`; never run on production startup."""

import os
from datetime import timedelta

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.auth import password_hasher
from app.config import Settings
from app.database import make_engine
from app.models import Order, OrderItem, User, utcnow

DEMO_PASSWORD = "Local-demo-only-42!"


def seed_demo(engine: Engine, password: str = DEMO_PASSWORD) -> None:
    with Session(engine) as db, db.begin():
        for email, name, role, reference in [
            ("alice@example.test", "Alice", "customer", "SP-1001"),
            ("bob@example.test", "Bob", "customer", "SP-2001"),
            ("support@example.test", "Support", "support", None),
        ]:
            user = db.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email, name=name, role=role, password_hash=password_hasher.hash(password)
                )
                db.add(user)
                db.flush()
            if reference and db.scalar(select(Order).where(Order.reference == reference)) is None:
                order = Order(
                    reference=reference,
                    customer_id=user.id,
                    status="shipped",
                    estimated_delivery=utcnow() + timedelta(days=3),
                )
                db.add(order)
                db.flush()
                db.add(OrderItem(order_id=order.id, name="Everyday canvas tote", quantity=1))


def main() -> None:
    settings = Settings()
    if settings.app_env != "development":
        raise SystemExit("Demo seeding is allowed only in development")
    engine = make_engine(settings)
    try:
        seed_demo(engine, os.environ.get("DEMO_SEED_PASSWORD", DEMO_PASSWORD))
    finally:
        engine.dispose()
    print("Synthetic accounts and orders are ready. See README for local sign-in details.")


if __name__ == "__main__":
    main()
