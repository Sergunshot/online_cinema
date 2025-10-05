import uuid

from sqlalchemy import (
    String,
    Float,
    Text,
    DECIMAL,
    UniqueConstraint,
    ForeignKey,
    Table,
    Column,
    Integer,
)
from typing import TYPE_CHECKING
from sqlalchemy.orm import mapped_column, Mapped, relationship

from .base import Base

from .accounts import User
if TYPE_CHECKING:
    from .orders import OrderItem

MovieGenres = Table(
    "movie_genres",
    Base.metadata,
    Column(
        "movie_id",
        ForeignKey("movies.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "genre_id",
        ForeignKey("genres.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
)

MovieDirectors = Table(
    "movie_directors",
    Base.metadata,
    Column(
        "movie_id",
        ForeignKey("movies.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "director_id",
        ForeignKey("directors.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
)

MovieStars = Table(
    "movie_stars",
    Base.metadata,
    Column(
        "movie_id",
        ForeignKey("movies.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
    Column(
        "star_id",
        ForeignKey("stars.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
    ),
)


class Genre(Base):
    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    movies: Mapped[list["Movie"]] = relationship(
        "Movie", secondary=MovieGenres, back_populates="genres"
    )

    def __repr__(self):
        return f"<Genre(name='{self.name}')>"


class Star(Base):
    __tablename__ = "stars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    movies: Mapped[list["Movie"]] = relationship(
        "Movie", secondary=MovieStars, back_populates="stars"
    )

    def __repr__(self):
        return f"<Star(name='{self.name}')>"


class Director(Base):
    __tablename__ = "directors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=True)

    movies: Mapped[list["Movie"]] = relationship(
        "Movie", secondary=MovieDirectors, back_populates="directors"
    )

    def __repr__(self):
        return f"<Director(name='{self.name}')>"


class Certification(Base):
    __tablename__ = "certifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    movies: Mapped[list["Movie"]] = relationship(
        "Movie", back_populates="certification"
    )

    def __repr__(self):
        return f"<Certification(name='{self.name}')>"


class Movie(Base):
    __tablename__ = "movies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uuid: Mapped[str] = mapped_column(
        String(255), unique=True, default=lambda: str(uuid.uuid4()), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    time: Mapped[int] = mapped_column(Integer, nullable=False)
    imdb: Mapped[float] = mapped_column(Float, nullable=True, default=0.0)
    votes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    meta_score: Mapped[float] = mapped_column(Float, nullable=True, default=None)
    gross: Mapped[float] = mapped_column(Float, nullable=True, default=None)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    price: Mapped[float] = mapped_column(DECIMAL(10, 2), nullable=False)
    certification_id: Mapped[int] = mapped_column(
        ForeignKey("certifications.id"), nullable=False
    )
    certification: Mapped[Certification] = relationship(back_populates="movies")
    comments: Mapped[list["Comment"]] = relationship(
        "Comment", back_populates="movie", cascade="all, delete-orphan"
    )
    favorites: Mapped[list["Favorite"]] = relationship(
        "Favorite", back_populates="movie", cascade="all, delete-orphan"
    )
    ratings: Mapped[list["Rating"]] = relationship(
        "Rating", back_populates="movie", cascade="all, delete-orphan"
    )
    genres: Mapped[list["Genre"]] = relationship(
        "Genre", secondary=MovieGenres, back_populates="movies"
    )

    directors: Mapped[list["Director"]] = relationship(
        "Director", secondary=MovieDirectors, back_populates="movies"
    )

    stars: Mapped[list["Star"]] = relationship(
        "Star", secondary=MovieStars, back_populates="movies"
    )
    cart_items = relationship("CartItem", back_populates="movie")
    order_items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem", back_populates="movie"
    )

    __table_args__ = (UniqueConstraint("name", "year", "time", name="unique_movie"),)

    @classmethod
    def default_order_by(cls):
        return [cls.id.desc()]

    def __repr__(self):
        return f"<Movie(name='{self.name}', release_year='{self.year}', score={self.meta_score})>"


class Like(Base):
    __tablename__ = "likes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id"), nullable=False)


class Dislike(Base):
    __tablename__ = "dislikes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id"), nullable=False)


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id"), nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="comments")
    movie: Mapped[Movie] = relationship("Movie", back_populates="comments")
    answers: Mapped[list["AnswerComment"]] = relationship(
        "AnswerComment", back_populates="comment", cascade="all, delete-orphan"
    )


class AnswerComment(Base):
    __tablename__ = "answer_comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    comment_id: Mapped[int] = mapped_column(ForeignKey("comments.id"), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)

    comment: Mapped["Comment"] = relationship("Comment", back_populates="answers")
    user: Mapped[User] = relationship("User")


class Favorite(Base):
    __tablename__ = "favorites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id"), nullable=False)

    user: Mapped[User] = relationship("User", back_populates="favorites")
    movie: Mapped[Movie] = relationship("Movie", back_populates="favorites")

    __table_args__ = (UniqueConstraint("user_id", "movie_id", name="unique_favorite"),)


class Rating(Base):
    __tablename__ = "ratings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    movie_id: Mapped[int] = mapped_column(ForeignKey("movies.id"), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)

    user: Mapped[User] = relationship("User", back_populates="ratings")
    movie: Mapped[Movie] = relationship("Movie", back_populates="ratings")
