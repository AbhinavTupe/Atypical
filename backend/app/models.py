"""Pydantic schemas for request/response bodies."""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

OrderStatus = Literal["pending", "shipped", "delivered"]


class OrderCreate(BaseModel):
    customer_name: str
    product_name: str
    status: OrderStatus = "pending"


class OrderUpdate(BaseModel):
    customer_name: str | None = None
    product_name: str | None = None
    status: OrderStatus | None = None


class Order(BaseModel):
    id: int
    customer_name: str
    product_name: str
    status: OrderStatus
    updated_at: datetime
