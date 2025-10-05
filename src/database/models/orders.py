from datetime import datetime
from decimal import Decimal
from sqlalchemy import Enum as SQLEnum, text
from enum import Enum

from sqlalchemy import Integer, ForeignKey, DECIMAL, DateTime, func
from sqlalchemy.orm import relationship, Mapped, mapped_column

from typing import List, TYPE_CHECKING
from .base import Base

if TYPE_CHECKING:
    from .accounts import User
    from .payments import Payment, PaymentItem
    from .movies import Movie


class OrderStatusEnum(Enum):
    PENDING = "pending"
    PAID = "paid"
    CANCELED = "canceled"


order_status_enum = SQLEnum(
    OrderStatusEnum,
    values_callable=lambda x: [member.value for member in x],
    native_enum=False,
    name="orderstatus_enum"
)


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    status: Mapped[OrderStatusEnum] = mapped_column(
        order_status_enum,
        default=OrderStatusEnum.PENDING,
        nullable=False
    )
    total_amount: Mapped[Decimal] = mapped_column(
        DECIMAL(10, 2),
        nullable=False,
        default=Decimal("0.00"),
        server_default=text("0.00"),
    )
    user: Mapped["User"] = relationship("User", back_populates="orders")
    items: Mapped[List["OrderItem"]] = relationship("OrderItem", back_populates="order")
    payments: Mapped[List["Payment"]] = relationship(
        "Payment", back_populates="order", cascade="all, delete-orphan"
    )

    def __repr__(self):
        return (
            f"<Order(id={self.id}, user_id={self.user_id}, "
            f"status={self.status}, total_amount={self.total_amount})>"
        )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id", ondelete="CASCADE"), nullable=False)
    price_at_order: Mapped[Decimal] = mapped_column(DECIMAL(10, 2), nullable=False)
    order: Mapped["Order"] = relationship("Order", back_populates="items")
    movie: Mapped["Movie"] = relationship("Movie", back_populates="order_items")
    payment_items: Mapped[List["PaymentItem"]] = relationship("PaymentItem", back_populates="order_item")

    def __repr__(self):
        return (
            f"<OrderItem(id={self.id}, order_id={self.order_id}, "
            f"movie_id={self.movie_id}, price_at_order={self.price_at_order})>"
        )
