from typing import cast

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import HttpUrl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_s3_storage_client, get_jwt_auth_manager
from database import get_db
from database.models.accounts import (
    User,
    UserProfile,
    GenderEnum,
    UserGroup,
    UserGroupEnum,
)
from exceptions import BaseSecurityError, S3FileUploadError
from schemas.profile import ProfileCreateSchema, ProfileResponseSchema
from security.interfaces import JWTAuthManagerInterface
from security.http import get_token
from storages import S3StorageInterface

router = APIRouter()


@router.post(
    "/users/{user_id}/profile/",
    response_model=ProfileResponseSchema,
    summary="Create user profile",
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    user_id: int,
    token: str = Depends(get_token),
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
    db: AsyncSession = Depends(get_db),
    s3_client: S3StorageInterface = Depends(get_s3_storage_client),
    profile_data: ProfileCreateSchema = Depends(ProfileCreateSchema.from_form),
) -> ProfileResponseSchema:
    """
    Creates a user profile.

    Steps:
    - Validate user authentication token.
    - Check if the user already has a profile.
    - Upload avatar to S3 storage.
    - Store profile details in the database.

    Args:
        user_id (int): The ID of the user for whom the profile is being created.
        token (str): The authentication token.
        jwt_manager (JWTAuthManagerInterface): JWT manager for decoding tokens.
        db (AsyncSession): The asynchronous database session.
        s3_client (S3StorageInterface): The asynchronous S3 storage client.
        profile_data (ProfileCreateSchema): The profile data from the form.

    Returns:
        ProfileResponseSchema: The created user profile details.

    Raises:
        HTTPException: If authentication fails, if the user is not found or inactive,
                       or if the profile already exists, or if S3 upload fails.
    """
    try:
        payload = jwt_manager.decode_access_token(token)
        token_user_id = payload.get("user_id")
    except BaseSecurityError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))

    if user_id != token_user_id:
        stmt = select(UserGroup).join(User).where(User.id == token_user_id)
        result = await db.execute(stmt)
        user_group = result.scalars().first()
        if not user_group or user_group.name == UserGroupEnum.USER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You don't have permission to edit this profile.",
            )

    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    user = result.scalars().first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or not active.",
        )

    stmt_profile = select(UserProfile).where(UserProfile.user_id == user.id)
    result_profile = await db.execute(stmt_profile)
    existing_profile = result_profile.scalars().first()
    if existing_profile:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User already has a profile.",
        )

    avatar_bytes = await profile_data.avatar.read()
    avatar_key = f"avatars/{user.id}_{profile_data.avatar.filename}"

    try:
        await s3_client.upload_file(file_name=avatar_key, file_data=avatar_bytes)
    except S3FileUploadError as e:
        print(f"Error uploading avatar to S3: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload avatar. Please try again later.",
        )

    new_profile = UserProfile(
        user_id=cast(int, user.id),
        first_name=profile_data.first_name,
        last_name=profile_data.last_name,
        gender=cast(GenderEnum, profile_data.gender),
        date_of_birth=profile_data.date_of_birth,
        info=profile_data.info,
        avatar=avatar_key,
    )

    db.add(new_profile)
    await db.commit()
    await db.refresh(new_profile)

    avatar_url = await s3_client.get_file_url(new_profile.avatar)

    return ProfileResponseSchema(
        id=new_profile.id,
        user_id=new_profile.user_id,
        first_name=new_profile.first_name,
        last_name=new_profile.last_name,
        gender=new_profile.gender,
        date_of_birth=new_profile.date_of_birth,
        info=new_profile.info,
        avatar=cast(HttpUrl, avatar_url),
    )
