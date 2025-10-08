import os
import re
from typing import Awaitable, Callable

from fastapi import Depends, HTTPException, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.cyextension.processors import date_cls
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from starlette import status

from config.settings import TestingSettings, Settings
from config import BaseAppSettings
from database.models.accounts import User, UserGroupEnum, UserGroup, UserProfile, GenderEnum
from database import get_db
from exceptions import BaseSecurityError, TokenExpiredError, S3FileUploadError
from notifications import EmailSenderInterface, EmailSender
from security import get_token
from security.interfaces import JWTAuthManagerInterface
from security.token_manager import JWTAuthManager
from storages import S3StorageInterface, S3StorageClient


def get_settings() -> Settings:
    """
    Retrieve the application settings based on the current environment.

    This function reads the 'ENVIRONMENT' environment variable (defaulting to 'developing' if not set)
    and returns a corresponding settings instance. If the environment is 'testing', it returns an instance
    of TestingSettings; otherwise, it returns an instance of Settings.

    Returns:
        Settings: The settings instance appropriate for the current environment.
    """
    environment = os.getenv("ENVIRONMENT", "developing")
    if environment == "testing":
        return TestingSettings()  # type: ignore
    return Settings()


def get_jwt_auth_manager(
    settings: BaseAppSettings = Depends(get_settings),
) -> JWTAuthManagerInterface:
    """
    Create and return a JWT authentication manager instance.

    This function uses the provided application settings to instantiate a JWTAuthManager, which implements
    the JWTAuthManagerInterface. The manager is configured with secret keys for access and refresh tokens
    as well as the JWT signing algorithm specified in the settings.

    Args:
        settings (BaseAppSettings, optional): The application settings instance.
        Defaults to the output of get_settings().

    Returns:
        JWTAuthManagerInterface: An instance of JWTAuthManager configured with
        the appropriate secret keys and algorithm.
    """
    return JWTAuthManager(
        secret_key_access=settings.SECRET_KEY_ACCESS,
        secret_key_refresh=settings.SECRET_KEY_REFRESH,
        algorithm=settings.JWT_SIGNING_ALGORITHM,
    )


def get_accounts_email_notificator(
    settings: BaseAppSettings = Depends(get_settings),
) -> EmailSenderInterface:
    """
    Retrieve an instance of the EmailSenderInterface configured with the application settings.

    This function creates an EmailSender using the provided settings, which include details such as the email host,
    port, credentials, TLS usage, and the directory and filenames for email templates. This allows the application
    to send various email notifications (e.g., activation, password reset) as required.

    Args:
        settings (BaseAppSettings, optional): The application settings,
        provided via dependency injection from `get_settings`.

    Returns:
        EmailSenderInterface: An instance of EmailSender configured with the appropriate email settings.
    """
    return EmailSender(
        hostname=settings.EMAIL_HOST,
        port=settings.EMAIL_PORT,
        email=settings.EMAIL_HOST_USER,
        password=settings.EMAIL_HOST_PASSWORD,
        use_tls=settings.EMAIL_USE_TLS,
        template_dir=settings.PATH_TO_EMAIL_TEMPLATES_DIR,
        # For accounts
        activation_email_template_name=settings.ACTIVATION_EMAIL_TEMPLATE_NAME,
        activation_complete_email_template_name=settings.ACTIVATION_COMPLETE_EMAIL_TEMPLATE_NAME,
        password_email_template_name=settings.PASSWORD_RESET_TEMPLATE_NAME,
        password_complete_email_template_name=settings.PASSWORD_RESET_COMPLETE_TEMPLATE_NAME,
        password_change_email_template_name=settings.PASSWORD_CHANGE_NAME,
        # For payments
        send_payment_email_template_name=settings.SEND_PAYMENT_EMAIL_TEMPLATE_NAME,
        send_refund_email_template_name=settings.SEND_REFUND_EMAIL_TEMPLATE_NAME,
        send_cancellation_email_template_name=settings.SEND_CANCELLATION_EMAIL_TEMPLATE_NAME,
    )


def get_s3_storage_client(
    settings: BaseAppSettings = Depends(get_settings),
) -> S3StorageInterface:
    """
    Retrieve an instance of the S3StorageInterface configured with the application settings.

    This function instantiates an S3StorageClient using the provided settings, which include the S3 endpoint URL,
    access credentials, and the bucket name. The returned client can be used to interact with an S3-compatible
    storage service for file uploads and URL generation.

    Args:
        settings (BaseAppSettings, optional): The application settings,
        provided via dependency injection from `get_settings`.

    Returns:
        S3StorageInterface: An instance of S3StorageClient configured with the appropriate S3 storage settings.
    """
    return S3StorageClient(
        endpoint_url=settings.S3_STORAGE_ENDPOINT,
        access_key=settings.S3_STORAGE_ACCESS_KEY,
        secret_key=settings.S3_STORAGE_SECRET_KEY,
        bucket_name=settings.S3_BUCKET_NAME,
    )


def get_s3_storage(
    client: S3StorageInterface = Depends(get_s3_storage_client),
) -> S3StorageInterface:
    return client


async def get_current_user_id(
    token: str = Depends(get_token),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> int:
    """
    Extracts the user ID from the provided JWT token.
    """
    try:
        payload = jwt_manager.decode_access_token(token)
        user_id = int(payload.get("user_id"))
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid token: user_id missing",
            )
        return user_id
    except BaseSecurityError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    token: str = Depends(get_token),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> User:
    """
    Dependency that verifies the JWT token and returns the current user.
    """
    try:
        payload = jwt_manager.decode_access_token(token)
        user_id: int = payload.get("user_id")

        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
            )

        user = await db.scalar(
            select(User).options(joinedload(User.group)).where(User.id == user_id)
        )

        if not isinstance(user, User):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
            )

        return user
    except TokenExpiredError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has expired.",
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
        )


async def require_moderator(current_user: User = Depends(get_current_user_id)) -> User:
    if current_user.group.name != UserGroupEnum.MODERATOR:
        raise HTTPException(status_code=403, detail="Access forbidden: moderator or admins only")
    return current_user


async def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.group.name != UserGroupEnum.ADMIN:
        raise HTTPException(status_code=403, detail="Access forbidden: admins only")
    return current_user


def allow_roles(*roles) -> Callable[..., Awaitable[User]]:
    async def dependency(user: User = Depends(get_current_user)) -> User:
        if user.group.name not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Permission denied: you don't have the required permissions to perform this action. "
            )
        return user

    return dependency


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("Authorization")
    if not auth:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not authenticated")
    scheme, _, param = auth.partition(" ")
    if scheme.lower() != "bearer" or not param:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid authentication credentials")
    return param


def _decode_token_or_401(jwt_manager: JWTAuthManagerInterface, token: str) -> int:
    try:
        payload = jwt_manager.decode_access_token(token)
        token_user_id = payload.get("user_id")
    except BaseSecurityError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
    if token_user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token payload.")
    return int(token_user_id)


async def _get_active_user_or_401(db: AsyncSession, user_id: int) -> User:
    user = await db.get(User, user_id)
    if not user or not getattr(user, "is_active", False):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or not active.")
    return user


async def _ensure_can_edit_target(db: AsyncSession, me_id: int, target_id: int) -> None:
    if target_id == me_id:
        return
    stmt = select(UserGroup).join(User).where(User.id == me_id)
    result = await db.execute(stmt)
    user_group = result.scalars().first()
    if not user_group or user_group.name == UserGroupEnum.USER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have permission to edit this profile.",
        )


async def _ensure_target_active(db: AsyncSession, target_id: int) -> User:
    user = await db.get(User, target_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found or not active.")
    return user


async def _ensure_no_profile(db: AsyncSession, user_id: int) -> None:
    exists = (
        await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalars().first()
    if exists:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already has a profile.")


def _validate_names(first_name: str, last_name: str) -> tuple[str, str]:
    name_re = re.compile(r"^[A-Za-z]+$")
    if not name_re.fullmatch(first_name):
        raise HTTPException(status_code=422, detail=f"{first_name} contains non-english letters")
    if not name_re.fullmatch(last_name):
        raise HTTPException(status_code=422, detail=f"{last_name} contains non-english letters")
    return first_name.lower(), last_name.lower()


def _parse_gender(gender_raw: str) -> GenderEnum:
    try:
        return GenderEnum(gender_raw)
    except Exception:
        raise HTTPException(status_code=422, detail="Gender must be one of: man, woman.")


def _parse_and_validate_dob(dob_raw: str) -> date_cls:
    try:
        dob = date_cls.fromisoformat(dob_raw)
    except Exception:
        raise HTTPException(status_code=422, detail="Invalid birth date - year must be greater than 1900.")
    if dob.year <= 1900:
        raise HTTPException(status_code=422, detail="Invalid birth date - year must be greater than 1900.")
    today = date_cls.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    if age < 18:
        raise HTTPException(status_code=422, detail="You must be at least 18 years old to register.")
    return dob


async def _read_and_validate_avatar(avatar_file: UploadFile | None) -> tuple[bytes, str]:
    allowed_types = {"image/jpeg", "image/png"}
    content_type = getattr(avatar_file, "content_type", None)
    if not avatar_file or content_type not in allowed_types:
        raise HTTPException(status_code=422, detail="Invalid image format")
    content = await avatar_file.read()
    if len(content) > 1 * 1024 * 1024:
        raise HTTPException(status_code=422, detail="Image size exceeds 1 MB")
    return content, content_type or "image/jpeg"


async def _upload_avatar_or_500(
    s3_client: S3StorageInterface, key: str, content: bytes, content_type: str
) -> None:
    try:
        try:
            await s3_client.upload_file(key, content, content_type=content_type)
        except TypeError:
            await s3_client.upload_file(file_name=key, file_data=content)
    except S3FileUploadError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload avatar. Please try again later.",
        )
