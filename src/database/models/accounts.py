import enum
from datetime import datetime, date, timezone, timedelta
from typing import Optional, List, TYPE_CHECKING

from sqlalchemy import Integer, Enum, String, DateTime, func, Boolean, ForeignKey, Date, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from .base import Base
from database.validators import accounts as validators
from security.passwords import hash_password, verify_password
from security.utils import generate_secure_token

if TYPE_CHECKING:
    from .cart import Cart
    from .orders import Order, Payment


class UserGroupEnum(str, enum.Enum):
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


class GenderEnum(str, enum.Enum):
    MAN = "man"
    WOMAN = "woman"


class UserGroup(Base):
    __tablename__ = "user_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[UserGroupEnum] = mapped_column(
        Enum(UserGroupEnum), unique=True, nullable=False
    )

    users = relationship("User", back_populates="group")

    def __repr__(self) -> str:
        return f"<UserGroup (id: {self.id}, name: {self.name}>"


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    _hashed_password: Mapped[str] = mapped_column("hashed_password", String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    group_id: Mapped[int] = mapped_column(ForeignKey("user_groups.id", ondelete="CASCADE"), nullable=False)
    group: Mapped["UserGroup"] = relationship(UserGroup, back_populates="users")
    profile: Mapped["UserProfile"] = relationship("UserProfile", back_populates="user", uselist=False)
    activation_token: Mapped["ActivationToken"] = relationship("ActivationToken", back_populates="user", uselist=False)
    password_reset_token: Mapped["PasswordResetToken"] = relationship(
        "PasswordResetToken", back_populates="user", uselist=False
    )
    refresh_tokens: Mapped["RefreshToken"] = relationship("RefreshToken", back_populates="user")

    ratings = relationship("Rating", back_populates="user")
    comments = relationship("Comment", back_populates="user")
    favorites = relationship("Favorite", back_populates="user")
    cart: Mapped[list["Cart"]] = relationship("Cart", back_populates="user")
    orders: Mapped[List["Order"]] = relationship("Order", back_populates="user")
    payments: Mapped[List["Payment"]] = relationship("Payment", back_populates="user")

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email}, is_active={self.is_active})>"

    def has_group(self, group_name: UserGroupEnum) -> bool:
        return self.group.name == group_name

    @classmethod
    def create(
            cls, email: str, raw_password: str, group_id: int | Mapped[int]
    ) -> "User":
        """
        Factory method to create a new UserModel instance.

        This method simplifies the creation of a new user by handling
        password hashing and setting required attributes.
        """
        user = cls(email=email, group_id=group_id)
        user.password = raw_password
        return user

    @property
    def password(self) -> None:
        raise AttributeError(
            "Password is write-only. Use the setter to set the password."
        )

    @password.setter
    def password(self, raw_password: str) -> None:
        """
        Set the user's password after validating its strength and hashing it.
        """
        validators.validate_password_strength(raw_password)
        self._hashed_password = hash_password(raw_password)

    def verify_password(self, raw_password: str) -> bool:
        """
        Verify the provided password against the stored hashed password.
        """
        return verify_password(raw_password, self._hashed_password)

    @validates("email")
    def validate_email(self, key, value):
        return validators.validate_email(value.lower())


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    first_name: Mapped[Optional[str]] = mapped_column(String(100))
    last_name: Mapped[Optional[str]] = mapped_column(String(100))
    avatar: Mapped[Optional[str]] = mapped_column(String(255))
    gender: Mapped[Optional[GenderEnum]] = mapped_column(Enum(GenderEnum))
    date_of_birth: Mapped[Optional[date]] = mapped_column(Date)
    info: Mapped[Optional[str]] = mapped_column(Text)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    user: Mapped[User] = relationship("User", back_populates="profile")

    __table_args__ = (UniqueConstraint("user_id"),)

    def __repr__(self):
        return (
            f"<UserProfile(id={self.id}, first_name={self.first_name}, last_name={self.last_name}, "
            f"gender={self.gender}, date_of_birth={self.date_of_birth})>"
        )


class TokenBase(Base):
    __abstract__ = True

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    token: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, default=generate_secure_token
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc) + timedelta(days=1),
    )

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )


class ActivationToken(TokenBase):
    __tablename__ = "activation_tokens"

    user: Mapped[User] = relationship("User", back_populates="activation_token")

    __table_args__ = (UniqueConstraint("user_id"),)

    def __repr__(self):
        return f"<ActivationToken(id={self.id}, token={self.token}, expires_at={self.expires_at})>"

    @classmethod
    def is_expired(cls, token: "ActivationToken", current_time: datetime) -> bool:
        return token.expires_at < current_time

    @classmethod
    def generate_new_token(cls, user_id: int) -> "ActivationToken":
        new_token = generate_secure_token()
        expiration_time = datetime.now(timezone.utc) + timedelta(hours=24)
        return cls(user_id=user_id, token=new_token, expires_at=expiration_time)


class PasswordResetToken(TokenBase):
    __tablename__ = "password_reset_tokens"

    user: Mapped[User] = relationship("User", back_populates="password_reset_token")

    __table_args__ = (UniqueConstraint("user_id"),)

    def __repr__(self):
        return f"<PasswordResetToken(id={self.id}, token={self.token}, expires_at={self.expires_at})>"


class RefreshToken(TokenBase):
    __tablename__ = "refresh_tokens"

    user: Mapped[User] = relationship("User", back_populates="refresh_tokens")
    token: Mapped[str] = mapped_column(
        String(512), unique=True, nullable=False, default=generate_secure_token
    )

    @classmethod
    def create(
        cls, user_id: int | Mapped[int], days_valid: int, token: str
    ) -> "RefreshToken":
        """
        Factory method to create a new RefreshTokenModel instance.

        This method simplifies the creation of a new refresh token by calculating
        the expiration date based on the provided number of valid days and setting
        the required attributes.
        """
        expires_at = datetime.now(timezone.utc) + timedelta(days=days_valid)
        return cls(user_id=user_id, expires_at=expires_at, token=token)

    def __repr__(self):
        return f"<RefreshTokenModel(id={self.id}, token={self.token}, expires_at={self.expires_at})>"
