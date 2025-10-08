from datetime import datetime, timezone

from fastapi import APIRouter, status, BackgroundTasks, Depends, HTTPException, Query, Body
from sqlalchemy import delete, or_
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from typing import cast

from sqlalchemy.orm import selectinload

from config import BaseAppSettings
from database import get_db
from database.models import (
    User,
    UserGroup,
    UserGroupEnum,
    ActivationToken,
    RefreshToken,
    PasswordResetToken,
)
from exceptions import BaseSecurityError
from notifications import EmailSenderInterface

from config.dependencies import (
    get_accounts_email_notificator,
    get_settings,
    get_jwt_auth_manager,
    get_current_user_id,
)
from schemas.accounts import (
    UserRegistrationResponseSchema,
    UserRegistrationRequestSchema,
    UserActivationRequestSchema,
    UserLoginResponseSchema,
    UserLoginRequestSchema,
    MessageResponseSchema,
    PasswordResetRequestSchema,
    PasswordResetCompleteRequestSchema,
    PasswordChangeRequestSchema,
    TokenRefreshResponseSchema,
    TokenRefreshRequestSchema,
)
from security.interfaces import JWTAuthManagerInterface

router = APIRouter()
BASE_URL = "http://127.0.0.1/accounts"


@router.post(
    "/register/",
    response_model=UserRegistrationResponseSchema,
    summary="User Registration",
    description="Register a new user with an email and password.",
    status_code=status.HTTP_201_CREATED,
    responses={
        409: {
            "description": "Conflict - User with this email already exists.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "A user with this email test@example.com already exists."
                    }
                }
            },
        },
        500: {
            "description": "Internal Server Error - An error occurred during user creation.",
            "content": {
                "application/json": {
                    "example": {"detail": "An error occurred during user creation."}
                }
            },
        },
    },
)
async def register_user(
    user_data: UserRegistrationRequestSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator),
) -> UserRegistrationResponseSchema:
    """
    Endpoint for user registration.

    Registers a new user, hashes their password, and assigns them to the default user group.
    If a user with the same email already exists, an HTTP 409 error is raised.
    In case of any unexpected issues during the creation process, an HTTP 500 error is returned.
    """
    try:
        if getattr(user_data, "group", None):
            try:
                user_group_enum = UserGroupEnum[user_data.group.upper()]
            except KeyError:
                raise HTTPException(status_code=422, detail=f"Unknown group: {user_data.group}")
        else:
            user_group_enum = UserGroupEnum.USER

        group_result = await db.execute(
            select(UserGroup).where(
                or_(
                    UserGroup.name == user_group_enum,
                    UserGroup.name == getattr(user_group_enum, "value", user_group_enum),
                )
            )
        )
        group = group_result.scalars().first()
        if not group:
            group = UserGroup(name=getattr(user_group_enum, "value", user_group_enum))
            db.add(group)
            await db.flush()

        new_user = User(email=user_data.email, password=user_data.password, group=group)
        db.add(new_user)
        await db.flush()

        activation_token = ActivationToken.generate_new_token(user_id=new_user.id)
        db.add(activation_token)
        await db.flush()

        await db.commit()

        background_tasks.add_task(
            email_sender.send_activation_email,
            new_user.email,
            f"{BASE_URL}/activate/?token={activation_token.token}",
        )

        group_name_out = getattr(new_user.group.name, "value", new_user.group.name)

        return UserRegistrationResponseSchema(
            id=new_user.id,
            email=new_user.email,
            group=group_name_out,
        )

    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"A user with this email {user_data.email} already exists.",
        )
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(
            status_code=500, detail="An error occurred during user creation."
        )


@router.post(
    "/activate/",
    response_model=MessageResponseSchema,
    summary="Activate User Account",
    description="Activate a user's account using their email and activation token.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - The activation token is invalid or expired, "
            "or the user account is already active.",
            "content": {
                "application/json": {
                    "examples": {
                        "invalid_token": {
                            "summary": "Invalid Token",
                            "value": {"detail": "Invalid or expired activation token."},
                        },
                        "already_active": {
                            "summary": "Account Already Active",
                            "value": {"detail": "User account is already active."},
                        },
                    }
                }
            },
        },
    },
)
async def activate_account(
    background_tasks: BackgroundTasks,
    activation_data: UserActivationRequestSchema | None = Body(None),
    token_q: str | None = Query(None, alias="token"),
    db: AsyncSession = Depends(get_db),
    email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator),
) -> MessageResponseSchema:
    token_value = (activation_data.token if activation_data and activation_data.token else None) or token_q
    if not token_value:
        raise HTTPException(status_code=400, detail="Activation token is required.")

    stmt = (
        select(ActivationToken)
        .options(selectinload(ActivationToken.user))
        .where(ActivationToken.token == token_value)
    )
    token_record = (await db.execute(stmt)).scalar_one_or_none()
    if not token_record:
        raise HTTPException(status_code=400, detail="Invalid or expired activation token.")

    expires_at = token_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        await db.delete(token_record)
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired activation token.")

    user = token_record.user
    if getattr(user, "is_active", False):
        raise HTTPException(status_code=400, detail="User account is already active.")

    user.is_active = True
    await db.delete(token_record)
    await db.commit()

    login_url = f"{BASE_URL}/login/" if BASE_URL else "/login/"
    background_tasks.add_task(
        email_sender.send_activation_complete_email,
        user.email,
        login_url,
    )

    return MessageResponseSchema(message="User account activated successfully.")


@router.post(
    "/activate_resend/",
    summary="Resend Activation Token",
    description="Resend the activation token if the previous one expired.",
    status_code=status.HTTP_200_OK,
    responses={
        404: {
            "description": "User Not Found - The user does not exist.",
            "content": {"application/json": {"example": {"detail": "User not found."}}},
        },
        400: {
            "description": "Bad Request - Invalid or expired activation token.",
            "content": {
                "application/json": {
                    "example": {"detail": "Activation token expired or invalid."}
                }
            },
        },
    },
)
async def resend_activation_token(
    user_data: UserRegistrationRequestSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator),
):
    """
    Endpoint to resend the activation token if the previous one expired.
    """
    result = await db.execute(select(User).filter(User.email == user_data.email))
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    result = await db.execute(
        select(ActivationToken).filter(ActivationToken.user_id == db_user.id)
    )
    activation_token = result.scalars().first()
    if activation_token:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Activation token is still valid.",
        )

    new_activation_token = ActivationToken(user_id=db_user.id, user=db_user)
    db.add(new_activation_token)
    await db.flush()
    await db.refresh(new_activation_token)
    await db.commit()

    background_tasks.add_task(
        email_sender.send_activation_email,
        db_user.email,
        f"{BASE_URL}/activate/?token={new_activation_token.token}",
    )

    return MessageResponseSchema(message="Activation token resent successfully.")


@router.post(
    "/login/",
    response_model=UserLoginResponseSchema,
    summary="User Login",
    description="Authenticate a user and return access and refresh tokens.",
    status_code=status.HTTP_201_CREATED,
    responses={
        401: {
            "description": "Unauthorized - Invalid email or password.",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid email or password."}
                }
            },
        },
        403: {
            "description": "Forbidden - User account is not activated.",
            "content": {
                "application/json": {
                    "example": {"detail": "User account is not activated."}
                }
            },
        },
        500: {
            "description": "Internal Server Error - An error occurred while processing the request.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "An error occurred while processing the request."
                    }
                }
            },
        },
    },
)
async def login_user(
        login_data: UserLoginRequestSchema,
        db: AsyncSession = Depends(get_db),
        settings: BaseAppSettings = Depends(get_settings),
        jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> UserLoginResponseSchema:
    """
    Endpoint for user login.

    Authenticates a user using their email and password.
    If authentication is successful, creates a new refresh token and returns both access and refresh tokens.

    Args:
        login_data (UserLoginRequestSchema): The login credentials.
        db (AsyncSession): The asynchronous database session.
        settings (BaseAppSettings): The application settings.
        jwt_manager (JWTAuthManagerInterface): The JWT authentication manager.

    Returns:
        UserLoginResponseSchema: A response containing the access and refresh tokens.

    Raises:
        HTTPException:
            - 401 Unauthorized if the email or password is invalid.
            - 403 Forbidden if the user account is not activated.
            - 500 Internal Server Error if an error occurs during token creation.
    """
    stmt = select(User).filter_by(email=login_data.email)
    result = await db.execute(stmt)
    user = result.scalars().first()

    if not user or not user.verify_password(login_data.password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is not activated.",
        )

    jwt_refresh_token = jwt_manager.create_refresh_token({"user_id": user.id})

    try:
        refresh_token = RefreshToken.create(
            user_id=user.id,
            days_valid=settings.LOGIN_TIME_DAYS,
            token=jwt_refresh_token
        )
        db.add(refresh_token)
        await db.flush()
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while processing the request.",
        )

    jwt_access_token = jwt_manager.create_access_token({"user_id": user.id})
    return UserLoginResponseSchema(
        access_token=jwt_access_token,
        refresh_token=jwt_refresh_token,
    )


@router.post(
    "/logout/",
    summary="User Logout",
    description="Revoke the refresh token and log the user out.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - The provided refresh token is invalid or expired.",
            "content": {
                "application/json": {"example": {"detail": "Invalid refresh token."}}
            },
        },
        401: {
            "description": "Unauthorized - Refresh token not found.",
            "content": {
                "application/json": {"example": {"detail": "Refresh token not found."}}
            },
        },
    },
)
async def logout_user(
    db: AsyncSession = Depends(get_db),
    current_user_id: int = Depends(get_current_user_id),
) -> MessageResponseSchema:
    """
    Logout endpoint that revokes the refresh token.
    """
    result = await db.execute(select(User).filter_by(id=current_user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="User not found"
        )

    result = await db.execute(select(RefreshToken).filter_by(user_id=user.id))
    refresh_token_record = result.scalars().first()
    if not refresh_token_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found.",
        )

    await db.delete(refresh_token_record)
    await db.commit()

    return MessageResponseSchema(message="Logout successful.")


@router.post(
    "/password-reset/request/",
    response_model=MessageResponseSchema,
    summary="Request Password Reset Token",
    description=(
        "Allows a user to request a password reset token. If the user exists and is active, "
        "a new token will be generated and any existing tokens will be invalidated."
    ),
    status_code=status.HTTP_200_OK,
)
async def request_password_reset_token(
    data: PasswordResetRequestSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator),
) -> MessageResponseSchema:
    """
    Endpoint to request a password reset token.

    If the user exists and is active, invalidates any existing password reset tokens and generates a new one.
    Always responds with a success message to avoid leaking user information.
    """
    result = await db.execute(select(User).filter_by(email=data.email))
    user = result.scalars().first()
    if not user or not user.is_active:
        return MessageResponseSchema(
            message="If you are registered, you will receive an email with instructions."
        )

    await db.execute(delete(PasswordResetToken).filter_by(user_id=user.id))
    new_reset_token = PasswordResetToken(user_id=cast(int, user.id))
    db.add(new_reset_token)
    await db.commit()

    background_tasks.add_task(
        email_sender.send_password_reset_email,
        str(data.email),
        f"{BASE_URL}/password-reset/complete/?token={new_reset_token.token}",
    )

    return MessageResponseSchema(
        message="If you are registered, you will receive an email with instructions."
    )


@router.post(
    "/reset-password/complete/",
    response_model=MessageResponseSchema,
    summary="Reset User Password",
    description="Reset a user's password if a valid token is provided.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - The provided email or token is invalid,"
            "the token has expired, or the user account is not active.",
            "content": {
                "application/json": {
                    "examples": {
                        "invalid_email_or_token": {
                            "summary": "Invalid Email or Token",
                            "value": {"detail": "Invalid email or token."},
                        },
                        "expired_token": {
                            "summary": "Expired Token",
                            "value": {"detail": "Invalid email or token."},
                        },
                    }
                }
            },
        },
        500: {
            "description": "Internal Server Error - An error occurred while resetting the password.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "An error occurred while resetting the password."
                    }
                }
            },
        },
    },
)
async def reset_password(
    data: PasswordResetCompleteRequestSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator)
) -> MessageResponseSchema:
    result = await db.execute(select(User).filter_by(email=data.email))
    user = result.scalars().first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email or token."
        )

    result_token = await db.execute(
        select(PasswordResetToken).filter_by(user_id=user.id)
    )
    token_record = result_token.scalars().first()
    if not token_record or token_record.token != data.token:
        if token_record:
            await db.delete(token_record)
            await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email or token."
        )

    expires_at = token_record.expires_at
    if not isinstance(expires_at, datetime):
        await db.delete(token_record)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email or token."
        )

    if expires_at.tzinfo is None:
        expires_at_utc = expires_at.replace(tzinfo=timezone.utc)
    else:
        expires_at_utc = expires_at.astimezone(timezone.utc)

    if expires_at_utc <= datetime.now(timezone.utc):
        await db.delete(token_record)
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email or token."
        )

    try:
        user.password = data.password
        await db.delete(token_record)
        await db.commit()
    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while resetting the password.",
        )

    login_url = f"{BASE_URL}/login/" if BASE_URL else "/login/"
    background_tasks.add_task(
        email_sender.send_password_reset_complete_email,
        user.email,
        login_url,
    )

    return MessageResponseSchema(message="Password reset successfully.")


@router.post(
    "/change-password/",
    response_model=MessageResponseSchema,
    summary="Changing password",
    description="Changing password using the transferred email, old and new password",
    responses={
        400: {
            "description": "Bad Request - Invalid email or password.",
            "content": {
                "application/json": {
                    "example": {"detail": "Invalid email or password."}
                }
            },
        },
        500: {
            "description": "Internal Server Error - An error occurred during user login.",
            "content": {
                "application/json": {
                    "example": {
                        "detail": "An error occurred while changing the password.."
                    }
                }
            },
        },
    },
    status_code=status.HTTP_200_OK,
)
async def request_change_password(
    user_data: PasswordChangeRequestSchema,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user_id: User = Depends(get_current_user_id),
    email_sender: EmailSenderInterface = Depends(get_accounts_email_notificator),
) -> MessageResponseSchema:
    result = await db.execute(select(User).filter_by(id=user_id))
    user = result.scalars().first()
    if not user.verify_password(raw_password=user_data.password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid email or password."
        )

    if user.verify_password(raw_password=user_data.new_password):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot assign the same password.",
        )

    try:
        user.password = user_data.new_password
        await db.execute(delete(RefreshToken).filter_by(user_id=user.id))
        await db.commit()

    except SQLAlchemyError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while changing the password.",
        )

    background_tasks.add_task(
        email_sender.send_password_change,
        str(user_data.email),
    )

    return MessageResponseSchema(message="Password changed successfully")


@router.post(
    "/refresh/",
    response_model=TokenRefreshResponseSchema,
    summary="Refresh Access Token",
    description="Refresh the access token using a valid refresh token.",
    status_code=status.HTTP_200_OK,
    responses={
        400: {
            "description": "Bad Request - The provided refresh token is invalid or expired.",
            "content": {
                "application/json": {"example": {"detail": "Token has expired."}}
            },
        },
        401: {
            "description": "Unauthorized - Refresh token not found.",
            "content": {
                "application/json": {"example": {"detail": "Refresh token not found."}}
            },
        },
        404: {
            "description": "Not Found - The user associated with the token does not exist.",
            "content": {"application/json": {"example": {"detail": "User not found."}}},
        },
    },
)
async def refresh_access_token(
    token_data: TokenRefreshRequestSchema,
    db: AsyncSession = Depends(get_db),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
) -> TokenRefreshResponseSchema:
    """
    Endpoint to refresh an access token.

    Validates the provided refresh token, extracts the user ID from it, and issues
    a new access token. If the token is invalid or expired, an error is returned.
    """
    try:
        decoded_token = jwt_manager.decode_refresh_token(token_data.refresh_token)
        user_id = decoded_token.get("user_id")
    except BaseSecurityError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        )

    result = await db.execute(
        select(RefreshToken).filter_by(token=token_data.refresh_token)
    )
    refresh_token_exist = result.scalars().first()
    if not refresh_token_exist:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token not found.",
        )

    result = await db.execute(select(User).filter_by(id=user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found.",
        )

    new_access_token = jwt_manager.create_access_token({"user_id": user_id})
    await db.execute(delete(RefreshToken).filter_by(token=token_data.refresh_token))
    await db.commit()

    return TokenRefreshResponseSchema(
        access_token=new_access_token, token_type="bearer"
    )
