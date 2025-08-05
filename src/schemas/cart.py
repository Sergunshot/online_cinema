from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict, model_validator

from schemas.movies import GenreSchema


class MovieInCartSchema(BaseModel):
    id: int
    name: str
    genres: List[GenreSchema]
    price: float = Field(..., ge=0)

    date: Optional[date] = None
    release_year: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def fill_release_year(self):
        if self.date:
            self.release_year = self.date.year
        return self


class CartItemBaseSchema(BaseModel):
    movie_id: int = Field(..., description="Movie ID")


class CartItemResponseSchema(BaseModel):
    id: int
    cart_id: int
    added_at: datetime
    movie: MovieInCartSchema

    model_config = ConfigDict(from_attributes=True)


class CartCreateSchema(BaseModel):
    user_id: int = Field(..., description="USER ID")


class CartResponseSchema(BaseModel):
    id: int
    user_id: int
    cart_items: List[CartItemResponseSchema]

    model_config = ConfigDict(from_attributes=True)
