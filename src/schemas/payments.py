from decimal import Decimal
from datetime import datetime
from typing import List, Optional
from pydantic import BaseModel, ConfigDict
from database.models import PaymentStatusEnum


class PaymentItemCreate(BaseModel):
    order_item_id: int
    price_at_payment: Decimal

    model_config = ConfigDict(from_attributes=True)


class PaymentCreate(BaseModel):
    order_id: int
    amount: Decimal
    payment_method: Optional[str]
    payment_items: List[PaymentItemCreate]
    external_payment_id: str

    model_config = ConfigDict(from_attributes=True)


class PaymentItemResponse(BaseModel):
    id: int
    payment_id: int
    order_item_id: int
    price_at_payment: Decimal

    model_config = ConfigDict(from_attributes=True)


class PaymentResponse(BaseModel):
    id: int
    user_id: int
    order_id: int
    created_at: datetime
    status: PaymentStatusEnum
    amount: Decimal
    external_payment_id: Optional[str]
    payment_method: Optional[str]
    client_secret: Optional[str] = None
    payment_items: List[PaymentItemResponse] = []

    model_config = ConfigDict(from_attributes=True)
