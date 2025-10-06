from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from io import BytesIO
from PIL import Image
from sqlalchemy import select, func

from database.models.accounts import User, UserProfile
from exceptions import S3FileUploadError


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_user_profile_with_fake_s3(
        db_session, seed_user_groups, reset_db, jwt_manager, s3_storage_fake, client
):
    """
    Positive test for creating a user profile.

    Steps:
    1. Create a test user and activate them.
    2. Generate an access token using `jwt_manager`.
    3. Send a profile creation request with an avatar.
    4. Verify that the avatar was uploaded to `FakeS3Storage`.
    5. Verify that the profile was created in the database.
    """
    user = User.create(email="test@mate.com", raw_password="TestPassword123!", group_id=1)
    user.is_active = True
    db_session.add(user)
    await db_session.commit()

    stmt = select(User).where(User.email == "test@mate.com")
    result = await db_session.execute(stmt)
    user = result.scalars().first()

    access_token = jwt_manager.create_access_token({"user_id": user.id})

    img = Image.new("RGB", (100, 100), color="blue")
    img_bytes = BytesIO()
    img.save(img_bytes, format="JPEG")
    img_bytes.seek(0)

    avatar_key = f"avatars/{user.id}_avatar.jpg"
    profile_url = f"/api/v1/profiles/users/{user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "This is a test profile."),
        "avatar": ("avatar.jpg", img_bytes, "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)
    assert response.status_code == 201, f"Expected 201, got {response.status_code}"
    profile_data = response.json()

    assert profile_data["first_name"] == "john", "First name does not match."
    assert profile_data["last_name"] == "doe", "Last name does not match."
    assert profile_data["gender"] == "man", "Gender does not match."
    assert profile_data["date_of_birth"] == "1990-01-01", "Date of birth does not match."
    assert "avatar" in profile_data, "Avatar URL is missing!"

    assert avatar_key in s3_storage_fake.storage, "Avatar file was not uploaded to Fake S3 Storage!"
    expected_url = f"http://fake-s3.local/{avatar_key}"
    actual_url = await s3_storage_fake.get_file_url(avatar_key)
    assert actual_url == expected_url, "Avatar URL does not match expected URL."

    stmt_profile = select(UserProfile).where(UserProfile.user_id == user.id)
    result_profile = await db_session.execute(stmt_profile)
    profile_in_db = result_profile.scalars().first()
    assert profile_in_db, f"Profile for user {user.id} should exist!"

    assert profile_in_db.first_name == "john", "First name is incorrect!"
    assert profile_in_db.last_name == "doe", "Last name is incorrect!"
    assert profile_in_db.gender == "man", "Gender is incorrect!"
    assert str(profile_in_db.date_of_birth) == "1990-01-01", "Date of birth is incorrect!"
    assert profile_in_db.info == "This is a test profile.", "Profile info is incorrect!"
    assert profile_in_db.avatar == avatar_key, "Avatar key in database does not match!"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize(
    "headers, expected_status, expected_detail",
    [
        (None, 403, "Not authenticated"),
        ({"Authorization": "Token invalid_token"}, 403, "Invalid authentication credentials"),
    ],
)
async def test_create_user_profile_invalid_auth(client, headers, expected_status, expected_detail):
    """
    Tests profile creation with missing or incorrectly formatted Authorization header.

    Expected:
    - 403 and "Not authenticated" if the header is missing.
    - 403 and "Invalid authentication credentials" if the format is incorrect.
    """
    profile_url = "/api/v1/profiles/users/1/profile/"
    response = await client.post(profile_url, headers=headers)
    assert response.status_code == expected_status, f"Expected {expected_status}, got {response.status_code}"
    assert response.json()["detail"] == expected_detail, f"Unexpected error message: {response.json()['detail']}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_create_user_profile_expired_token(client, jwt_manager):
    """
    Test profile creation with an expired access token.

    Expected result:
    - The request should fail with 401 Unauthorized.
    - The error message should be: "Token has expired."
    """
    expired_time = datetime.now() - timedelta(days=1)
    with patch("security.token_manager.datetime") as mock_datetime:
        mock_datetime.now.return_value = expired_time
        expired_token = jwt_manager.create_access_token({"user_id": 1})

    profile_url = "/api/v1/profiles/users/1/profile/"
    headers = {"Authorization": f"Bearer {expired_token}"}

    img = Image.new("RGB", (100, 100), color="blue")
    img_bytes = BytesIO()
    img.save(img_bytes, format="JPEG")
    img_bytes.seek(0)

    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "Test profile."),
        "avatar": ("avatar.jpg", img_bytes, "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)

    assert response.status_code == 401, f"Expected 401, got {response.status_code}"
    assert response.json()["detail"] == "Token has expired.", \
        f"Unexpected error message: {response.json()['detail']}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_admin_creates_user_profile(
        db_session, seed_user_groups, reset_db, jwt_manager, s3_storage_fake, client
):
    """
    Test that an admin can create a profile for another user.

    Steps:
    1. Create an admin user and a regular user.
    2. Generate an access token for the admin.
    3. Send a request to create a profile for the regular user.
    4. Verify that the avatar was uploaded to FakeS3Storage.
    5. Verify that the profile was created in the database.
    """
    admin_user = User.create(email="admin@mate.com", raw_password="AdminPass123!", group_id=3)
    admin_user.is_active = True
    db_session.add(admin_user)

    regular_user = User.create(email="user@mate.com", raw_password="UserPass123!", group_id=1)
    regular_user.is_active = True
    db_session.add(regular_user)

    await db_session.commit()

    stmt = select(User).where(User.email.in_(["admin@mate.com", "user@mate.com"]))
    result = await db_session.execute(stmt)
    users = result.scalars().all()

    users_dict = {user.email: user for user in users}
    admin_user = users_dict["admin@mate.com"]
    regular_user = users_dict["user@mate.com"]

    admin_token = jwt_manager.create_access_token({"user_id": admin_user.id})

    img = Image.new("RGB", (100, 100), color="blue")
    img_bytes = BytesIO()
    img.save(img_bytes, format="JPEG")
    img_bytes.seek(0)

    avatar_key = f"avatars/{regular_user.id}_avatar.jpg"
    profile_url = f"/api/v1/profiles/users/{regular_user.id}/profile/"
    headers = {"Authorization": f"Bearer {admin_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "Test profile."),
        "avatar": ("avatar.jpg", img_bytes, "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)
    assert response.status_code == 201, f"Expected 201, got {response.status_code}"
    profile_data = response.json()

    assert profile_data["first_name"] == "john"
    assert profile_data["last_name"] == "doe"
    assert profile_data["gender"] == "man"
    assert profile_data["date_of_birth"] == "1990-01-01"
    assert "avatar" in profile_data, "Avatar URL is missing!"

    assert avatar_key in s3_storage_fake.storage, "Avatar file was not uploaded to Fake S3 Storage!"
    expected_url = f"http://fake-s3.local/{avatar_key}"
    actual_url = await s3_storage_fake.get_file_url(avatar_key)
    assert actual_url == expected_url, "Avatar URL does not match expected URL."

    stmt_profile = select(UserProfile).where(UserProfile.user_id == regular_user.id)
    result_profile = await db_session.execute(stmt_profile)
    profile_in_db = result_profile.scalars().first()
    assert profile_in_db, f"Profile for user {regular_user.id} should exist!"

    assert profile_in_db.first_name == "john", "First name is incorrect!"
    assert profile_in_db.last_name == "doe", "Last name is incorrect!"
    assert profile_in_db.gender == "man", "Gender is incorrect!"
    assert str(profile_in_db.date_of_birth) == "1990-01-01", "Date of birth is incorrect!"
    assert profile_in_db.info == "Test profile.", "Profile info is incorrect!"
    assert profile_in_db.avatar == avatar_key, "Avatar key in database does not match!"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_user_cannot_create_another_user_profile(
        db_session, seed_user_groups, reset_db, jwt_manager, s3_storage_fake, client
):
    """
    Test that a regular user cannot create a profile for another user.

    Steps:
    1. Create two regular users.
    2. Generate an access token for the first user.
    3. Attempt to create a profile for the second user.
    4. Verify that the request fails with 403 Forbidden and that no profile is created.
    """
    user_1 = User.create(email="user1@mate.com", raw_password="User1Pass123!", group_id=1)  # 1 = User
    user_1.is_active = True
    db_session.add(user_1)

    user_2 = User.create(email="user2@mate.com", raw_password="User2Pass123!", group_id=1)  # 1 = User
    user_2.is_active = True
    db_session.add(user_2)

    await db_session.commit()

    stmt = select(User).where(User.email.in_(["user1@mate.com", "user2@mate.com"]))
    result = await db_session.execute(stmt)
    users = result.scalars().all()
    users_dict = {user.email: user for user in users}
    user_1 = users_dict["user1@mate.com"]
    user_2 = users_dict["user2@mate.com"]

    user_1_token = jwt_manager.create_access_token({"user_id": user_1.id})

    img = Image.new("RGB", (100, 100), color="red")
    img_bytes = BytesIO()
    img.save(img_bytes, format="JPEG")
    img_bytes.seek(0)

    profile_url = f"/api/v1/profiles/users/{user_2.id}/profile/"
    headers = {"Authorization": f"Bearer {user_1_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "Attempting unauthorized profile creation."),
        "avatar": ("avatar.jpg", img_bytes, "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)
    assert response.status_code == 403, f"Expected 403, got {response.status_code}"
    assert response.json()["detail"] == "You don't have permission to edit this profile.", \
        f"Unexpected error message: {response.json()['detail']}"

    stmt_profile = select(UserProfile).where(UserProfile.user_id == user_2.id)
    result_profile = await db_session.execute(stmt_profile)
    profile_in_db = result_profile.scalars().first()
    assert profile_in_db is None, "Profile should not have been created!"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_inactive_user_cannot_create_profile(
        db_session, seed_user_groups, reset_db, jwt_manager, s3_storage_fake, client
):
    """
    Test that an inactive user cannot create a profile.

    Steps:
    1. Create a user but do not activate them.
    2. Generate an access token for the user.
    3. Attempt to create a profile.
    4. Verify that the request fails with 401 Unauthorized and that no profile is created.
    """
    user = User.create(email="inactive@mate.com", raw_password="TestPassword123!", group_id=1)
    user.is_active = False
    db_session.add(user)
    await db_session.commit()

    stmt = select(User).where(User.email == "inactive@mate.com")
    result = await db_session.execute(stmt)
    user = result.scalars().first()

    access_token = jwt_manager.create_access_token({"user_id": user.id})

    img = Image.new("RGB", (100, 100), color="gray")
    img_bytes = BytesIO()
    img.save(img_bytes, format="JPEG")
    img_bytes.seek(0)

    profile_url = f"/api/v1/profiles/users/{user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "Attempting to create a profile while inactive."),
        "avatar": ("avatar.jpg", img_bytes, "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)
    assert response.status_code == 401, f"Expected 401, got {response.status_code}"
    assert response.json()[
               "detail"] == "User not found or not active.", f"Unexpected error message: {response.json()['detail']}"

    stmt_profile = select(UserProfile).where(UserProfile.user_id == user.id)
    result_profile = await db_session.execute(stmt_profile)
    profile_in_db = result_profile.scalars().first()
    assert profile_in_db is None, "Profile should not have been created!"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_cannot_create_profile_twice(
        db_session, seed_user_groups, reset_db, jwt_manager, s3_storage_fake, client
):
    """
    Test that a user cannot create a profile twice.

    Steps:
    1. Create and activate a user.
    2. Create a profile for the user.
    3. Attempt to create another profile.
    4. Verify that the request fails with 400 Bad Request and only one profile exists in the database.
    """
    user = User.create(email="test@mate.com", raw_password="TestPassword123!", group_id=1)
    user.is_active = True
    db_session.add(user)
    await db_session.commit()

    stmt_user = select(User).where(User.email == "test@mate.com")
    result_user = await db_session.execute(stmt_user)
    user = result_user.scalars().first()

    access_token = jwt_manager.create_access_token({"user_id": user.id})

    img = Image.new("RGB", (100, 100), color="blue")
    img_bytes = BytesIO()
    img.save(img_bytes, format="JPEG")
    img_bytes.seek(0)

    profile_url = f"/api/v1/profiles/users/{user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "This is a test profile."),
        "avatar": ("avatar.jpg", img_bytes, "image/jpeg"),
    }

    response1 = await client.post(profile_url, headers=headers, files=files)
    assert response1.status_code == 201, f"Expected 201, got {response1.status_code}"

    response2 = await client.post(profile_url, headers=headers, files=files)
    assert response2.status_code == 400, f"Expected 400, got {response2.status_code}"
    assert response2.json()["detail"] == "User already has a profile.", (
        f"Unexpected error message: {response2.json()['detail']}"
    )

    stmt_count = select(func.count(UserProfile.id)).where(UserProfile.user_id == user.id)
    result_count = await db_session.execute(stmt_count)
    profiles_count = result_count.scalar_one()
    assert profiles_count == 1, f"Expected only one profile, but found {profiles_count}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_profile_creation_fails_on_s3_upload_error(
        db_session, seed_user_groups, reset_db, jwt_manager, s3_storage_fake, client
):
    """
    Test that profile creation fails if S3 upload fails.

    Steps:
    1. Create and activate a user.
    2. Mock `s3_storage_fake.upload_file` to raise `S3FileUploadError`.
    3. Attempt to create a profile.
    4. Verify that the request fails with 500 Internal Server Error and no profile is created in the database.
    """
    user = User.create(email="test@mate.com", raw_password="TestPassword123!", group_id=1)
    user.is_active = True
    db_session.add(user)
    await db_session.commit()

    stmt = select(User).where(User.email == "test@mate.com")
    result = await db_session.execute(stmt)
    user = result.scalars().first()

    access_token = jwt_manager.create_access_token({"user_id": user.id})

    img = Image.new("RGB", (100, 100), color="blue")
    img_bytes = BytesIO()
    img.save(img_bytes, format="JPEG")
    img_bytes.seek(0)

    profile_url = f"/api/v1/profiles/users/{user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "This is a test profile."),
        "avatar": ("avatar.jpg", img_bytes, "image/jpeg"),
    }

    with patch.object(s3_storage_fake, "upload_file", side_effect=S3FileUploadError("Simulated S3 failure")):
        response = await client.post(profile_url, headers=headers, files=files)

    assert response.status_code == 500, f"Expected 500, got {response.status_code}"
    assert response.json()["detail"] == "Failed to upload avatar. Please try again later.", (
        f"Unexpected error message: {response.json()['detail']}"
    )

    stmt_profile = select(UserProfile).where(UserProfile.user_id == user.id)
    result_profile = await db_session.execute(stmt_profile)
    profile_in_db = result_profile.scalars().first()
    assert profile_in_db is None, "Profile should not be created when S3 upload fails!"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("first_name, last_name, expected_error", [
    ("John1", "Doe", "John1 contains non-english letters"),
    ("John", "Doe1", "Doe1 contains non-english letters"),
])
async def test_profile_creation_invalid_name(client, jwt_manager, test_user, first_name, last_name, expected_error):
    """
    Test that profile creation fails if the first_name or last_name contains non-English letters.

    This test sends a profile creation request with invalid names and expects a 422 response
    with an error message containing the specified error text.
    """
    access_token = jwt_manager.create_access_token({"user_id": test_user.id})

    profile_url = f"/api/v1/profiles/users/{test_user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, first_name),
        "last_name": (None, last_name),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "This is a test profile."),
        "avatar": ("avatar.jpg", BytesIO(b"fake_image"), "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)

    assert response.status_code == 422, f"Expected 422, got {response.status_code}"
    assert expected_error in str(response.json()), f"Unexpected error message: {response.json()}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_profile_creation_invalid_avatar_format(client, jwt_manager, test_user):
    """
    Test that profile creation fails if the avatar has an unsupported format.

    This test sends a profile creation request with an avatar file in GIF format,
    which is unsupported. It expects the endpoint to return a 422 status code with an
    error message indicating "Invalid image format".
    """
    access_token = jwt_manager.create_access_token({"user_id": test_user.id})

    profile_url = f"/api/v1/profiles/users/{test_user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "This is a test profile."),
        "avatar": ("avatar.gif", BytesIO(b"fake_image"), "image/gif"),
    }

    response = await client.post(profile_url, headers=headers, files=files)

    assert response.status_code == 422, f"Expected 422, got {response.status_code}"
    assert "Invalid image format" in str(response.json()), f"Unexpected error message: {response.json()}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_profile_creation_avatar_too_large(db_session, client, jwt_manager, test_user):
    """
    Test that profile creation fails if the avatar exceeds 1MB.

    This test attempts to create a profile using a large JPEG image (created in-memory)
    that exceeds the allowed size limit (1MB). It expects the endpoint to return a 422 status code
    with an error message indicating that the image size exceeds the allowed limit.
    """
    access_token = jwt_manager.create_access_token({"user_id": test_user.id})

    img = Image.new("RGB", (10000, 10000), color="blue")
    img_bytes = BytesIO()
    img.save(img_bytes, format="JPEG")
    img_bytes.seek(0)

    profile_url = f"/api/v1/profiles/users/{test_user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "This is a test profile."),
        "avatar": ("avatar.jpg", img_bytes, "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"
    assert "Image size exceeds 1 MB" in str(response.json()), f"Unexpected error message: {response.json()}"


@pytest.mark.asyncio
@pytest.mark.unit
async def test_profile_creation_invalid_gender(client, jwt_manager, test_user):
    """
    Test that profile creation fails if gender is invalid.

    This test sends a profile creation request with an invalid gender value ("other").
    It expects the endpoint to return a 422 status code with an error message indicating that
    the gender must be one of the allowed values.
    """
    access_token = jwt_manager.create_access_token({"user_id": test_user.id})

    profile_url = f"/api/v1/profiles/users/{test_user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "other"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, "This is a test profile."),
        "avatar": ("avatar.jpg", BytesIO(b"fake_image"), "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)

    assert response.status_code == 422, f"Expected 422, got {response.status_code}"
    assert "Gender must be one of" in str(response.json()), f"Unexpected error message: {response.json()}"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("birth_date, expected_error", [
    ("1800-01-01", "Invalid birth date - year must be greater than 1900."),
    ("2010-01-01", "You must be at least 18 years old to register."),
])
async def test_profile_creation_invalid_birth_date(client, jwt_manager, test_user, birth_date, expected_error):
    """
    Test that profile creation fails if birth_date is invalid.

    This test sends a profile creation request with an invalid birth_date value and expects
    the endpoint to return a 422 status code along with an appropriate error message.
    """
    access_token = jwt_manager.create_access_token({"user_id": test_user.id})
    profile_url = f"/api/v1/profiles/users/{test_user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, birth_date),
        "info": (None, "This is a test profile."),
        "avatar": ("avatar.jpg", BytesIO(b"fake_image"), "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"
    assert expected_error in str(response.json()), f"Unexpected error message: {response.json()}"


@pytest.mark.asyncio
@pytest.mark.unit
@pytest.mark.parametrize("info_value", ["", "   "])
async def test_profile_creation_empty_info(client, jwt_manager, test_user, info_value):
    """
    Test that profile creation fails if the info field is empty or contains only spaces.

    This test sends a profile creation request with an invalid info value and expects
    a 422 response with an error message indicating that the info field cannot be empty.
    """
    access_token = jwt_manager.create_access_token({"user_id": test_user.id})
    profile_url = f"/api/v1/profiles/users/{test_user.id}/profile/"
    headers = {"Authorization": f"Bearer {access_token}"}
    files = {
        "first_name": (None, "John"),
        "last_name": (None, "Doe"),
        "gender": (None, "man"),
        "date_of_birth": (None, "1990-01-01"),
        "info": (None, info_value),
        "avatar": ("avatar.jpg", BytesIO(b"fake_image"), "image/jpeg"),
    }

    response = await client.post(profile_url, headers=headers, files=files)
    assert response.status_code == 422, f"Expected 422, got {response.status_code}"
    assert "Info field cannot be empty or contain only spaces." in str(response.json()), \
        f"Unexpected error message: {response.json()}"
