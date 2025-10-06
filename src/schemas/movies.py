from datetime import datetime
from typing import Optional, List
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator, ConfigDict


class BaseSchema(BaseModel):
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class GenreSchema(BaseSchema):
    pass


class DirectorSchema(BaseSchema):
    pass


class StarSchema(BaseSchema):
    pass


class CertificationSchema(BaseSchema):
    pass


class AnswerCommentSchema(BaseModel):
    id: int
    user_id: int
    text: str

    model_config = ConfigDict(from_attributes=True)


class CommentSchema(BaseModel):
    id: int
    user_id: int
    comment: str
    answers: List[AnswerCommentSchema] = []

    model_config = ConfigDict(from_attributes=True)


class MovieBaseSchema(BaseModel):
    uuid: str | None = None
    name: str
    year: int
    time: int
    imdb: float
    meta_score: float | None = None
    gross: float | None = None
    description: str
    price: float

    model_config = ConfigDict(from_attributes=True)

    @field_validator("year")
    @classmethod
    def validate_year(cls, value):
        current_year = datetime.now().year
        if value > current_year + 1:
            raise ValueError(
                f"The year in 'year' cannot be greater than {current_year + 1}."
            )
        return value


class MovieDetailSchema(MovieBaseSchema):
    id: int
    genres: list[GenreSchema]
    stars: list[StarSchema]
    directors: list[DirectorSchema]
    certification: CertificationSchema
    comments: list[CommentSchema]

    model_config = ConfigDict(from_attributes=True)


class MovieListItemSchema(BaseModel):
    id: int
    name: str
    year: int
    time: int
    imdb: float
    genres: List[GenreSchema]
    price: float

    model_config = ConfigDict(from_attributes=True)


class MovieListResponseSchema(BaseModel):
    items: List[MovieListItemSchema]
    prev_page: Optional[str] = None
    next_page: Optional[str] = None
    total_pages: int
    total_items: int

    model_config = ConfigDict(from_attributes=True)


class MovieCreateSchema(BaseModel):
    uuid: str = Field(default_factory=lambda: str(uuid4()))
    name: str
    year: int
    time: int
    imdb: float = Field(..., ge=0, le=10)
    votes: int = 0
    meta_score: Optional[float] = None
    gross: Optional[float] = None
    description: str
    price: float = Field(..., ge=0)

    certification_id: int
    genre_ids: List[int] = Field(default_factory=list)
    star_ids: List[int] = Field(default_factory=list)
    director_ids: List[int] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class MovieUpdateSchema(BaseModel):
    name: Optional[str] = None
    year: Optional[int] = None
    time: Optional[int] = None
    imdb: Optional[float] = Field(None, ge=0, le=10)
    votes: Optional[int] = None
    meta_score: Optional[float] = None
    gross: Optional[float] = None
    description: Optional[str] = None
    price: Optional[float] = Field(None, ge=0)

    certification_id: Optional[int] = None
    genre_ids: Optional[List[int]] = None
    star_ids: Optional[List[int]] = None
    director_ids: Optional[List[int]] = None

    model_config = ConfigDict(from_attributes=True)
