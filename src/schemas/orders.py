from decimal import Decimal
from typing import Optional
from datetime import datetime

from typing import List

from pydantic import BaseModel


class OrderItemResponseSchema(BaseModel):
    movie_id: int
    price_at_order: Decimal


class OrderResponseSchema(BaseModel):
    id: int
    user_id: int
    created_at: datetime
    status: str
    total_amount: Decimal
    items: List[OrderItemResponseSchema]


class OrderWithMoviesResponseSchema(BaseModel):
    id: int
    user_id: int
    created_at: str
    status: str
    total_amount: Decimal
    movies: List[str]


class OrderListResponseSchema(BaseModel):
    orders: List[OrderWithMoviesResponseSchema]
    prev_page: Optional[str]
    next_page: Optional[str]
    total_pages: int
    total_items: int
