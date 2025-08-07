from decimal import Decimal
from datetime import datetime
import enum

from sqlalchemy import Integer, ForeignKey, DateTime, Numeric, Enum, String, func
from sqlalchemy.orm import relationship, Mapped, mapped_column

from .base import Base
from typing import List


class PaymentStatusEnum(enum.Enum):
    pending = "pending"
    successful = "successful"
    canceled = "canceled"
    refunded = "refunded"


class PaymentItem(Base):
    __tablename__ = "payment_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    payment_id: Mapped[int] = mapped_column(
        ForeignKey("payments.id", ondelete="CASCADE"), nullable=False
    )
    order_item_id: Mapped[int] = mapped_column(
        ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False
    )
    price_at_payment: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    payment: Mapped["Payment"] = relationship("Payment", back_populates="payment_items")
    order_item: Mapped["OrderItem"] = relationship(
        "OrderItem", back_populates="payment_items"
    )

    def __repr__(self):
        return (
            f"<PaymentItem(id={self.id}, payment_id={self.payment_id}, "
            f"order_item_id={self.order_item_id}, price_at_payment={self.price_at_payment})>"
        )


class Payment(Base):
    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    status: Mapped[PaymentStatusEnum] = mapped_column(
        Enum(PaymentStatusEnum), nullable=False, default=PaymentStatusEnum.successful
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    external_payment_id: Mapped[str] = mapped_column(String(255), nullable=True)
    payment_method: Mapped[str] = mapped_column(String(255), nullable=True)
    order: Mapped["Order"] = relationship("Order", back_populates="payments")
    user: Mapped["User"] = relationship("User", back_populates="payments")
    payment_items: Mapped[List["PaymentItem"]] = relationship(
        "PaymentItem", back_populates="payment", lazy="selectin"
    )

    def __repr__(self):
        return f"<Payment(id={self.id}, order_id={self.order_id}, amount={self.amount}, status={self.status})>"
