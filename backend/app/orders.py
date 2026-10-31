"""Authorized domain service, reused by HTTP and the agent's allowlisted tool."""

from typing import Protocol

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from app.auth import Principal
from app.models import Order, utcnow
from app.schemas import OrderStatus


class OrderUnavailable(Exception):
    """Identical failure for a missing order and another customer's order."""


class CommerceAdapter(Protocol):
    def lookup_order(self, principal: Principal, order_reference: str) -> OrderStatus: ...


class SyntheticCommerceAdapter:
    def __init__(self, engine: Engine):
        self.engine = engine

    def lookup_order(self, principal: Principal, order_reference: str) -> OrderStatus:
        if principal.role != "customer":
            raise OrderUnavailable()
        with Session(self.engine) as db:
            order = db.scalar(
                select(Order).where(
                    Order.reference == order_reference,
                    Order.customer_id == principal.id,
                )
            )
            if order is None:
                raise OrderUnavailable()
            return OrderStatus(
                order_reference=order.reference,
                status=order.status,
                estimated_delivery=order.estimated_delivery,
                checked_at=utcnow(),
            )
