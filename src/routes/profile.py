from fastapi import APIRouter, Depends, HTTPException, status, Request

from sqlalchemy.ext.asyncio import AsyncSession

from config.dependencies import get_jwt_auth_manager, get_s3_storage, _extract_bearer_token, _decode_token_or_401, \
    _get_active_user_or_401, _ensure_can_edit_target, _ensure_target_active, _ensure_no_profile, _validate_names, \
    _parse_gender, _parse_and_validate_dob, _read_and_validate_avatar, _upload_avatar_or_500
from database import get_db
from database.models.accounts import UserProfile

from schemas.profile import ProfileResponseSchema
from security.interfaces import JWTAuthManagerInterface
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
    request: Request,
    jwt_manager: JWTAuthManagerInterface = Depends(get_jwt_auth_manager),
    db: AsyncSession = Depends(get_db),
    s3_client: S3StorageInterface = Depends(get_s3_storage),
) -> ProfileResponseSchema:
    token = _extract_bearer_token(request)
    me_id = _decode_token_or_401(jwt_manager, token)
    await _get_active_user_or_401(db, me_id)
    await _ensure_can_edit_target(db, me_id, user_id)

    user = await _ensure_target_active(db, user_id)
    await _ensure_no_profile(db, user.id)

    form = await request.form()
    first_name_raw = (form.get("first_name") or "").strip()
    last_name_raw = (form.get("last_name") or "").strip()
    gender_raw = (form.get("gender") or "").strip()
    dob_raw = (form.get("date_of_birth") or "").strip()
    info = (form.get("info") or "")
    avatar_file = form.get("avatar")

    first_name, last_name = _validate_names(first_name_raw, last_name_raw)
    if not info.strip():
        raise HTTPException(status_code=422, detail="Info field cannot be empty or contain only spaces.")
    gender_enum = _parse_gender(gender_raw)
    dob = _parse_and_validate_dob(dob_raw)

    content, content_type = await _read_and_validate_avatar(avatar_file)
    avatar_key = f"avatars/{user.id}_avatar.jpg"
    await _upload_avatar_or_500(s3_client, avatar_key, content, content_type)

    profile = UserProfile(
        user_id=int(user.id),
        first_name=first_name,
        last_name=last_name,
        gender=gender_enum.value,
        date_of_birth=dob,
        info=info.strip(),
        avatar=avatar_key,
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)

    avatar_url = await s3_client.get_file_url(profile.avatar)

    return ProfileResponseSchema(
        id=profile.id,
        user_id=profile.user_id,
        first_name=profile.first_name,
        last_name=profile.last_name,
        gender=profile.gender,
        date_of_birth=profile.date_of_birth,
        info=profile.info,
        avatar=avatar_url,
    )
