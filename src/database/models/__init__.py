from sqlalchemy.orm import declarative_base

Base = declarative_base()
from .accounts import (
    UserGroupEnum,
    GenderEnum,
    UserGroup,
    User,
    UserProfile,
    ActivationToken,
    PasswordResetToken,
    RefreshToken,
)
from .cart import Cart, CartItem
from .movies import (
    MovieGenres,
    MovieDirectors,
    MovieStars,
    Genre,
    Star,
    Director,
    Certification,
    Movie,
    Like,
    Dislike,
    Comment,
    AnswerComment,
    Favorite,
    Rating,
)
