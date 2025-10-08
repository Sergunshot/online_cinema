import importlib
import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select, delete, func, create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import joinedload
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from database import (
    User,
    ActivationToken,
    PasswordResetToken,
    UserGroup,
    UserGroupEnum,
    RefreshToken, Base
)


@pytest.mark.asyncio
async def test_register_user_success(client, db_session, seed_user_groups):
    """
    Test successful user registration.

    Validates that a new user and an activation token are created in the database.
    """
    payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }

    response = await client.post("/api/v1/accounts/register/", json=payload)
    assert response.status_code == 201, "Expected status code 201 Created."
    response_data = response.json()
    assert response_data["email"] == payload["email"], "Returned email does not match."
    assert "id" in response_data, "Response does not contain user ID."

    stmt_user = select(User).where(User.email == payload["email"])
    result = await db_session.execute(stmt_user)
    created_user = result.scalars().first()
    assert created_user is not None, "User was not created in the database."
    assert created_user.email == payload["email"], "Created user's email does not match."

    stmt_token = select(ActivationToken).where(ActivationToken.user_id == created_user.id)
    result = await db_session.execute(stmt_token)
    activation_token = result.scalars().first()
    assert activation_token is not None, "Activation token was not created in the database."
    assert activation_token.user_id == created_user.id, "Activation token's user_id does not match."
    assert activation_token.token is not None, "Activation token has no token value."

    expires_at = activation_token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    assert expires_at > datetime.now(timezone.utc), "Activation token is already expired."


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_password, expected_error", [
    ("short", "Password must contain at least 8 characters."),
    ("NoDigitHere!", "Password must contain at least one digit."),
    ("nodigitnorupper@", "Password must contain at least one uppercase letter."),
    ("NOLOWERCASE1@", "Password must contain at least one lower letter."),
    ("NoSpecial123", "Password must contain at least one special character: @, $, !, %, *, ?, #, &."),
])
async def test_register_user_password_validation(client, seed_user_groups, invalid_password, expected_error):
    """
    Test password strength validation in the user registration endpoint.

    Ensures that when an invalid password is provided, the endpoint returns the appropriate
    error message and a 422 status code.

    Args:
        client: The asynchronous HTTP client fixture.
        seed_user_groups: Fixture that seeds the default user groups.
        invalid_password (str): The password to test.
        expected_error (str): The expected error message substring.
    """
    payload = {
        "email": "testuser@example.com",
        "password": invalid_password
    }

    response = await client.post("/api/v1/accounts/register/", json=payload)
    assert response.status_code == 422, "Expected status code 422 for invalid input."

    response_data = response.json()
    assert expected_error in str(response_data), f"Expected error message: {expected_error}"


@pytest.mark.asyncio
async def test_register_user_conflict(client, db_session, seed_user_groups):
    """
    Test user registration conflict.

    Ensures that trying to register a user with an existing email
    returns a 409 Conflict status and the correct error message.

    Args:
        client: The asynchronous HTTP client fixture.
        db_session: The asynchronous database session fixture.
        seed_user_groups: Fixture that seeds default user groups.
    """
    payload = {
        "email": "conflictuser@example.com",
        "password": "StrongPassword123!"
    }

    response_first = await client.post("/api/v1/accounts/register/", json=payload)
    assert response_first.status_code == 201, "Expected status code 201 for the first registration."

    stmt = select(User).where(User.email == payload["email"])
    result = await db_session.execute(stmt)
    created_user = result.scalars().first()
    assert created_user is not None, "User should be created after the first registration."

    response_second = await client.post("/api/v1/accounts/register/", json=payload)
    assert response_second.status_code == 409, "Expected status code 409 for a duplicate registration."

    response_data = response_second.json()
    expected_message = f"A user with this email {payload['email']} already exists."
    assert response_data["detail"] == expected_message, f"Expected error message: {expected_message}"


@pytest.mark.asyncio
async def test_register_user_internal_server_error(client, seed_user_groups):
    """
    Test server error during user registration.

    Ensures that a 500 Internal Server Error is returned when a database operation fails.

    This test patches the commit method of the AsyncSession to simulate a SQLAlchemyError,
    then verifies that the registration endpoint returns the appropriate HTTP 500 error
    with the expected error message.
    """
    payload = {
        "email": "erroruser@example.com",
        "password": "StrongPassword123!"
    }

    with patch("routes.accounts.AsyncSession.commit", side_effect=SQLAlchemyError):
        response = await client.post("/api/v1/accounts/register/", json=payload)

        assert response.status_code == 500, "Expected status code 500 for internal server error."

        response_data = response.json()
        expected_message = "An error occurred during user creation."
        assert response_data["detail"] == expected_message, f"Expected error message: {expected_message}"


@pytest.mark.asyncio
async def test_activate_account_success(client, db_session, seed_user_groups):
    """
    Test successful activation of a user account.

    Steps:
    - Register a new user.
    - Verify the user is inactive.
    - Activate the user using the activation token.
    - Verify the user is activated and the token is deleted.
    """
    registration_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }

    registration_response = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert registration_response.status_code == 201, "Expected status code 201 for successful registration."

    stmt = (
        select(User)
        .options(joinedload(User.activation_token))
        .where(User.email == registration_payload["email"])
    )
    result = await db_session.execute(stmt)
    user = result.scalars().first()
    assert user is not None, "User was not created in the database."
    assert not user.is_active, "Newly registered user should not be active."

    assert user.activation_token is not None and user.activation_token.token is not None, \
        "Activation token was not created in the database."

    activation_payload = {
        "email": registration_payload["email"],
        "token": user.activation_token.token
    }

    activation_response = await client.post("/api/v1/accounts/activate/", json=activation_payload)
    assert activation_response.status_code == 200, "Expected status code 200 for successful activation."
    assert activation_response.json()["message"] == "User account activated successfully."

    stmt = (
        select(User)
        .options(joinedload(User.activation_token))
        .where(User.email == registration_payload["email"])
    )
    result = await db_session.execute(stmt)
    user = result.scalars().first()
    await db_session.refresh(user)
    assert user.is_active, "User should be active after successful activation."

    stmt = select(ActivationToken).where(ActivationToken.user_id == user.id)
    result = await db_session.execute(stmt)
    token = result.scalars().first()
    assert token is None, "Activation token should be deleted after successful activation."


@pytest.mark.asyncio
async def test_activate_user_with_expired_token(client, db_session, seed_user_groups):
    """
    Test activation with an expired token.

    Ensures that the endpoint returns a 400 error when the activation token is expired.
    Steps:
    - Register a new user.
    - Retrieve the user and their activation token.
    - Manually set the token's expiration to a past date.
    - Attempt to activate the account with the expired token.
    - Verify that the response is a 400 error with the expected error message.
    """
    registration_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }
    registration_response = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert registration_response.status_code == 201, "Expected status code 201 for successful registration."

    stmt = select(User).where(User.email == registration_payload["email"])
    result = await db_session.execute(stmt)
    user = result.scalars().first()
    assert user is not None, "User should exist in the database."
    assert not user.is_active, "User should not be active before activation."

    stmt_token = select(ActivationToken).where(ActivationToken.user_id == user.id)
    result_token = await db_session.execute(stmt_token)
    activation_token = result_token.scalars().first()
    assert activation_token is not None, "Activation token should exist for the user."

    activation_token.expires_at = datetime.now(timezone.utc) - timedelta(days=2)
    await db_session.commit()

    activation_payload = {
        "email": registration_payload["email"],
        "token": activation_token.token
    }
    activation_response = await client.post("/api/v1/accounts/activate/", json=activation_payload)

    assert activation_response.status_code == 400, "Expected status code 400 for expired token."
    assert activation_response.json()["detail"] == "Invalid or expired activation token.", (
        "Expected error message for expired token."
    )


@pytest.mark.asyncio
async def test_activate_user_with_deleted_token(client, db_session, seed_user_groups):
    """
    Test activation with a deleted token.

    Ensures that the endpoint returns a 400 error when the activation token has been deleted.

    Steps:
    - Register a new user.
    - Verify that the user is created and inactive.
    - Delete the activation token from the database.
    - Attempt to activate the account using the deleted token.
    - Verify that a 400 error is returned with the appropriate error message.
    """
    registration_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }
    registration_response = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert registration_response.status_code == 201, "Expected status code 201 for successful registration."

    stmt = select(User).where(User.email == registration_payload["email"])
    result = await db_session.execute(stmt)
    user = result.scalars().first()
    assert user is not None, "User should exist in the database."
    assert not user.is_active, "User should not be active before activation."

    stmt_token = select(ActivationToken).where(ActivationToken.user_id == user.id)
    result_token = await db_session.execute(stmt_token)
    activation_token = result_token.scalars().first()
    assert activation_token is not None, "Activation token should exist for the user."

    token_value = activation_token.token

    await db_session.execute(
        delete(ActivationToken).where(ActivationToken.id == activation_token.id)
    )
    await db_session.commit()

    activation_payload = {
        "email": registration_payload["email"],
        "token": token_value
    }
    activation_response = await client.post("/api/v1/accounts/activate/", json=activation_payload)
    assert activation_response.status_code == 400, "Expected status code 400 for deleted token."
    assert activation_response.json()["detail"] == "Invalid or expired activation token.", (
        "Expected error message for deleted token."
    )


@pytest.mark.asyncio
async def test_activate_already_active_user(client, db_session, seed_user_groups):
    """
    Test activation of an already active user.

    Ensures that the endpoint returns a 400 error if the user is already active.
    Steps:
    - Register a new user.
    - Mark the user as active in the database.
    - Attempt to activate the user using the activation token.
    - Verify that a 400 error with the expected error message is returned.
    """
    registration_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }

    registration_response = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert registration_response.status_code == 201, "Expected status code 201 for successful registration."

    stmt = select(User).where(User.email == registration_payload["email"])
    result = await db_session.execute(stmt)
    user = result.scalars().first()
    assert user is not None, "User should exist in the database."

    user.is_active = True
    await db_session.commit()

    stmt_token = select(ActivationToken).where(ActivationToken.user_id == user.id)
    result_token = await db_session.execute(stmt_token)
    activation_token = result_token.scalars().first()
    assert activation_token is not None, "Activation token should exist for the user."

    activation_payload = {
        "email": registration_payload["email"],
        "token": activation_token.token
    }
    activation_response = await client.post("/api/v1/accounts/activate/", json=activation_payload)
    assert activation_response.status_code == 400, "Expected status code 400 for already active user."
    assert activation_response.json()["detail"] == "User account is already active.", (
        "Expected error message for already active user."
    )


@pytest.mark.asyncio
async def test_request_password_reset_token_success(client, db_session, seed_user_groups):
    """
    Test successful password reset token request.

    Ensures that a password reset token is created for an active user.

    Steps:
    - Register a new user.
    - Mark the user as active.
    - Request a password reset token.
    - Verify that the endpoint returns status 200 and the expected success message.
    - Query the database to confirm that a PasswordResetTokenModel record was created.
    - Verify that the token's expiration date is in the future.
    """
    registration_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }
    registration_response = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert registration_response.status_code == 201, "Expected status code 201 for successful registration."

    stmt = select(User).where(User.email == registration_payload["email"])
    result = await db_session.execute(stmt)
    user = result.scalars().first()
    assert user is not None, "User should exist in the database."

    user.is_active = True
    await db_session.commit()

    reset_payload = {"email": registration_payload["email"]}
    reset_response = await client.post("/api/v1/accounts/password-reset/request/", json=reset_payload)
    assert reset_response.status_code == 200, "Expected status code 200 for successful token request."
    assert reset_response.json()["message"] == "If you are registered, you will receive an email with instructions.", \
        "Expected success message for password reset token request."

    stmt_token = select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    result_token = await db_session.execute(stmt_token)
    reset_token = result_token.scalars().first()
    assert reset_token is not None, "Password reset token should be created for the user."

    expires_at = reset_token.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    assert expires_at > datetime.now(timezone.utc), "Password reset token should have a future expiration date."


@pytest.mark.asyncio
async def test_request_password_reset_token_nonexistent_user(client, db_session):
    """
    Test password reset token request for a non-existent user.

    Ensures that the endpoint responds with a generic success message and that no password reset token is created
    when the email does not exist in the database.
    """
    reset_payload = {"email": "nonexistent@example.com"}

    reset_response = await client.post("/api/v1/accounts/password-reset/request/", json=reset_payload)
    assert reset_response.status_code == 200, "Expected status code 200 for non-existent user request."
    assert reset_response.json()["message"] == "If you are registered, you will receive an email with instructions.", (
        "Expected generic success message for non-existent user request."
    )

    stmt = select(func.count(PasswordResetToken.id))
    result = await db_session.execute(stmt)
    reset_token_count = result.scalar_one()
    assert reset_token_count == 0, "No password reset token should be created for non-existent user."


@pytest.mark.asyncio
async def test_request_password_reset_token_for_inactive_user(client, db_session, seed_user_groups):
    """
    Test password reset token request for a registered but inactive user.

    Ensures that the endpoint returns the generic success message and that no password reset token
    is created when the user is registered but inactive.
    """
    registration_payload = {
        "email": "inactiveuser@example.com",
        "password": "StrongPassword123!"
    }
    registration_response = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert registration_response.status_code == 201, "Expected status code 201 for successful registration."

    stmt = select(User).where(User.email == registration_payload["email"])
    result = await db_session.execute(stmt)
    created_user = result.scalars().first()
    assert created_user is not None, "User should be created in the database."
    assert not created_user.is_active, "User should not be active after registration."

    reset_payload = {"email": registration_payload["email"]}
    reset_response = await client.post("/api/v1/accounts/password-reset/request/", json=reset_payload)
    assert reset_response.status_code == 200, "Expected status code 200 for inactive user password reset request."
    assert reset_response.json()["message"] == "If you are registered, you will receive an email with instructions.", (
        "Expected generic success message for inactive user password reset request."
    )

    stmt_tokens = select(func.count(PasswordResetToken.id))
    result_tokens = await db_session.execute(stmt_tokens)
    reset_token_count = result_tokens.scalar_one()
    assert reset_token_count == 0, "No password reset token should be created for an inactive user."


@pytest.mark.asyncio
async def test_reset_password_success(client, db_session, seed_user_groups):
    """
    Test the complete password reset flow.

    Steps:
    - Register a user.
    - Activate the user.
    - Request a password reset token.
    - Use the token to reset the password.
    - Verify the password is updated in the database.
    """
    registration_payload = {
        "email": "testuser@example.com",
        "password": "OldPassword123!"
    }
    registration_response = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert registration_response.status_code == 201, "Expected status code 201 for successful registration."

    stmt = select(User).where(User.email == registration_payload["email"])
    result = await db_session.execute(stmt)
    created_user = result.scalars().first()
    assert created_user is not None, "User should be created in the database."

    stmt_token = select(ActivationToken).where(ActivationToken.user_id == created_user.id)
    result_token = await db_session.execute(stmt_token)
    activation_token = result_token.scalars().first()
    assert activation_token is not None, "Activation token should be created in the database."

    activation_payload = {
        "email": registration_payload["email"],
        "token": activation_token.token
    }
    activation_response = await client.post("/api/v1/accounts/activate/", json=activation_payload)
    assert activation_response.status_code == 200, "Expected status code 200 for successful activation."

    await db_session.refresh(created_user)
    assert created_user.is_active, "User should be active after successful activation."

    reset_request_payload = {"email": registration_payload["email"]}
    reset_request_response = await client.post("/api/v1/accounts/password-reset/request/", json=reset_request_payload)
    assert reset_request_response.status_code == 200, "Expected status code 200 for password reset token request."

    stmt_reset = select(PasswordResetToken).where(PasswordResetToken.user_id == created_user.id)
    result_reset = await db_session.execute(stmt_reset)
    reset_token_record = result_reset.scalars().first()
    assert reset_token_record is not None, "Password reset token should be created in the database."

    new_password = "NewSecurePassword123!"
    reset_payload = {
        "email": registration_payload["email"],
        "token": reset_token_record.token,
        "password": new_password
    }
    reset_response = await client.post("/api/v1/accounts/reset-password/complete/", json=reset_payload)
    assert reset_response.status_code == 200, "Expected status code 200 for successful password reset."
    assert reset_response.json()["message"] == "Password reset successfully.", (
        "Unexpected response message for password reset."
    )

    await db_session.refresh(created_user)
    assert created_user.verify_password(new_password), "Password should be updated successfully in the database."


@pytest.mark.asyncio
async def test_reset_password_invalid_email(client, db_session):
    """
    Test password reset with an email that does not exist in the database.

    Validates that the endpoint returns a 400 status code and appropriate error message.
    """
    reset_payload = {
        "email": "nonexistent@example.com",
        "token": "random_token",
        "password": "NewSecurePassword123!"
    }

    response = await client.post("/api/v1/accounts/reset-password/complete/", json=reset_payload)

    assert response.status_code == 400, "Expected status code 400 for invalid email."
    assert response.json()["detail"] == "Invalid email or token.", "Unexpected error message."


@pytest.mark.asyncio
async def test_reset_password_invalid_token(client, db_session, seed_user_groups):
    """
    Test password reset with an incorrect token.

    Validates that the endpoint returns a 400 status code and an appropriate error message when an invalid token is provided.
    Also ensures that any invalid token is removed from the database.
    """
    registration_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }
    response = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert response.status_code == 201, "User registration failed."

    stmt = select(User).where(User.email == registration_payload["email"])
    result = await db_session.execute(stmt)
    user = result.scalars().first()
    assert user is not None, "User should exist in the database."

    user.is_active = True
    await db_session.commit()

    reset_request_payload = {"email": registration_payload["email"]}
    response = await client.post("/api/v1/accounts/password-reset/request/", json=reset_request_payload)
    assert response.status_code == 200, "Password reset request failed."

    reset_complete_payload = {
        "email": registration_payload["email"],
        "token": "incorrect_token",
        "password": "NewSecurePassword123!"
    }
    response = await client.post("/api/v1/accounts/reset-password/complete/", json=reset_complete_payload)
    assert response.status_code == 400, "Expected status code 400 for invalid token."
    assert response.json()["detail"] == "Invalid email or token.", "Unexpected error message."

    stmt_token = select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    result_token = await db_session.execute(stmt_token)
    token_record = result_token.scalars().first()
    assert token_record is None, "Invalid token was not removed."


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_reset_password_expired_token(client, db_session):
    """
    When the password reset token is expired, the API returns 400
    and the token is deleted from the database.
    """

    groups = [{"name": "USER"}, {"name": "MODERATOR"}, {"name": "ADMIN"}]
    stmt = sqlite_insert(UserGroup).values(groups).prefix_with("OR IGNORE")
    await db_session.execute(stmt)
    await db_session.commit()

    email = f"testuser+{uuid4().hex[:8]}@example.com"
    registration_payload = {"email": email, "password": "StrongPassword123!"}
    r = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert r.status_code == 201, f"User registration failed: {r.text}"

    user = (await db_session.execute(select(User).where(User.email == email))).scalars().first()
    assert user is not None, "User should exist in the database."

    user.is_active = True
    await db_session.commit()

    r2 = await client.post("/api/v1/accounts/password-reset/request/", json={"email": email})
    assert r2.status_code == 200, f"Password reset request failed: {r2.text}"

    token_record = (
        await db_session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        )
    ).scalars().first()
    assert token_record is not None, "Password reset token not created."

    token_record.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
    await db_session.commit()

    # --- Act
    r3 = await client.post(
        "/api/v1/accounts/reset-password/complete/",
        json={"email": email, "token": token_record.token, "password": "NewSecurePassword123!"},
    )

    # --- Assert
    assert r3.status_code == 400, "Expected status code 400 for expired token."
    body = r3.json()
    assert body.get("detail") == "Invalid email or token.", f"Unexpected error message: {body}"

    expired_token = (
        await db_session.execute(
            select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
        )
    ).scalar_one_or_none()
    assert expired_token is None, "Expired token was not removed."

    # --- Cleanup
    await db_session.execute(delete(PasswordResetToken).where(PasswordResetToken.user_id == user.id))
    await db_session.execute(delete(User).where(User.id == user.id))
    await db_session.commit()


@pytest.mark.asyncio
async def test_reset_password_sqlalchemy_error(client, db_session, seed_user_groups):
    """
    Test password reset when a database commit raises SQLAlchemyError.

    Validates that the endpoint returns a 500 Internal Server Error and the appropriate error message
    when an error occurs during the password reset process.

    Steps:
    - Register a new user.
    - Mark the user as active.
    - Request a password reset token.
    - Attempt to reset the password while simulating a database commit error.
    - Verify that a 500 error is returned with the expected error message.
    """
    registration_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }
    registration_response = await client.post("/api/v1/accounts/register/", json=registration_payload)
    assert registration_response.status_code == 201, "User registration failed."

    stmt = select(User).where(User.email == registration_payload["email"])
    result = await db_session.execute(stmt)
    user = result.scalars().first()
    assert user is not None, "User should exist in the database."

    user.is_active = True
    await db_session.commit()

    reset_request_payload = {"email": registration_payload["email"]}
    reset_request_response = await client.post("/api/v1/accounts/password-reset/request/", json=reset_request_payload)
    assert reset_request_response.status_code == 200, "Password reset request failed."

    stmt_token = select(PasswordResetToken).where(PasswordResetToken.user_id == user.id)
    result_token = await db_session.execute(stmt_token)
    token_record = result_token.scalars().first()
    assert token_record is not None, "Password reset token not created."

    reset_complete_payload = {
        "email": registration_payload["email"],
        "token": token_record.token,
        "password": "NewSecurePassword123!"
    }

    with patch("routes.accounts.AsyncSession.commit", side_effect=SQLAlchemyError):
        reset_response = await client.post("/api/v1/accounts/reset-password/complete/", json=reset_complete_payload)

    assert reset_response.status_code == 500, "Expected status code 500 for SQLAlchemyError."
    assert reset_response.json()["detail"] == "An error occurred while resetting the password.", (
        "Unexpected error message for SQLAlchemyError."
    )


@pytest.mark.asyncio
async def test_login_user_success(client, db_session, jwt_manager, seed_user_groups):
    """
    Test successful login.

    Validates that access and refresh tokens are returned, the refresh token is stored in the database,
    and both tokens are valid.
    """
    user_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }

    stmt = select(UserGroup).where(UserGroup.name == UserGroupEnum.USER)
    result = await db_session.execute(stmt)
    user_group = result.scalars().first()
    assert user_group is not None, "Default user group should exist."

    user = User.create(
        email=user_payload["email"],
        raw_password=user_payload["password"],
        group_id=user_group.id
    )
    user.is_active = True
    db_session.add(user)
    await db_session.commit()

    login_payload = {
        "email": user_payload["email"],
        "password": user_payload["password"]
    }
    response = await client.post("/api/v1/accounts/login/", json=login_payload)
    assert response.status_code == 201, "Expected status code 201 for successful login."
    response_data = response.json()
    assert "access_token" in response_data, "Access token is missing in the response."
    assert "refresh_token" in response_data, "Refresh token is missing in the response."
    assert response_data["access_token"], "Access token is empty."
    assert response_data["refresh_token"], "Refresh token is empty."

    access_token_data = jwt_manager.decode_access_token(response_data["access_token"])
    assert access_token_data["user_id"] == user.id, "Access token does not contain correct user ID."

    refresh_token_data = jwt_manager.decode_refresh_token(response_data["refresh_token"])
    assert refresh_token_data["user_id"] == user.id, "Refresh token does not contain correct user ID."

    stmt_refresh = select(RefreshToken).where(RefreshToken.user_id == user.id)
    result_refresh = await db_session.execute(stmt_refresh)
    refresh_token_record = result_refresh.scalars().first()
    assert refresh_token_record is not None, "Refresh token was not stored in the database."
    assert refresh_token_record.token == response_data["refresh_token"], "Stored refresh token does not match."

    expires_at = refresh_token_record.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    assert expires_at > datetime.now(timezone.utc), "Refresh token is already expired."


@pytest.mark.asyncio
async def test_login_user_invalid_cases(client, db_session, seed_user_groups):
    """
    Test login with invalid cases:
    1. Non-existent user.
    2. Incorrect password for an existing user.
    """
    login_payload = {
        "email": "nonexistent@example.com",
        "password": "SomePassword123!"
    }
    response = await client.post("/api/v1/accounts/login/", json=login_payload)
    assert response.status_code == 401, "Expected status code 401 for non-existent user."
    assert response.json()["detail"] == "Invalid email or password.", \
        "Unexpected error message for non-existent user."

    user_payload = {
        "email": "testuser@example.com",
        "password": "CorrectPassword123!"
    }
    stmt = select(UserGroup).where(UserGroup.name == UserGroupEnum.USER)
    result = await db_session.execute(stmt)
    user_group = result.scalars().first()
    assert user_group is not None, "Default user group should exist."

    user = User.create(
        email=user_payload["email"],
        raw_password=user_payload["password"],
        group_id=user_group.id
    )
    user.is_active = True
    db_session.add(user)
    await db_session.commit()

    login_payload_incorrect_password = {
        "email": user_payload["email"],
        "password": "WrongPassword123!"
    }
    response = await client.post("/api/v1/accounts/login/", json=login_payload_incorrect_password)
    assert response.status_code == 401, "Expected status code 401 for incorrect password."
    assert response.json()["detail"] == "Invalid email or password.", \
        "Unexpected error message for incorrect password."


@pytest.mark.asyncio
async def test_login_user_inactive_account(client, db_session, seed_user_groups):
    """
    Test login with an inactive user account.

    Validates that the endpoint returns a 403 status code and an appropriate error message
    when attempting to log in with a user whose account is not activated.
    """
    user_payload = {
        "email": "inactiveuser@example.com",
        "password": "StrongPassword123!"
    }

    stmt = select(UserGroup).where(UserGroup.name == UserGroupEnum.USER)
    result = await db_session.execute(stmt)
    user_group = result.scalars().first()
    assert user_group is not None, "User group not found."

    user = User.create(
        email=user_payload["email"],
        raw_password=user_payload["password"],
        group_id=user_group.id
    )
    user.is_active = False
    db_session.add(user)
    await db_session.commit()

    login_payload = {
        "email": user_payload["email"],
        "password": user_payload["password"]
    }
    response = await client.post("/api/v1/accounts/login/", json=login_payload)

    assert response.status_code == 403, "Expected status code 403 for inactive user."
    assert response.json()["detail"] == "User account is not activated.", \
        "Unexpected error message for inactive user."


@pytest.mark.asyncio
async def test_login_user_commit_error(client, db_session, seed_user_groups):
    """
    Test login when a database commit error occurs.

    Validates that the endpoint returns a 500 status code and an appropriate error message.
    """
    user_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }
    stmt = select(UserGroup).where(UserGroup.name == UserGroupEnum.USER)
    result = await db_session.execute(stmt)
    user_group = result.scalars().first()
    assert user_group is not None, "Default user group should exist."

    user = User.create(
        email=user_payload["email"],
        raw_password=user_payload["password"],
        group_id=user_group.id
    )
    user.is_active = True
    db_session.add(user)
    await db_session.commit()

    login_payload = {
        "email": user_payload["email"],
        "password": user_payload["password"]
    }

    with patch("routes.accounts.AsyncSession.commit", side_effect=SQLAlchemyError):
        response = await client.post("/api/v1/accounts/login/", json=login_payload)

    assert response.status_code == 500, "Expected status code 500 for database commit error."
    assert response.json()["detail"] == "An error occurred while processing the request.", (
        "Unexpected error message for database commit error."
    )


@pytest.mark.asyncio
async def test_refresh_access_token_success(client, db_session, jwt_manager, seed_user_groups):
    """
    Test successful access token refresh.

    Validates that a new access token is returned when a valid refresh token is provided.
    Steps:
    - Create an active user in the database.
    - Log in the user to obtain a refresh token.
    - Use the refresh token to obtain a new access token.
    - Verify that the new access token contains the correct user ID.
    """
    user_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }
    stmt = select(UserGroup).where(UserGroup.name == UserGroupEnum.USER)
    result = await db_session.execute(stmt)
    user_group = result.scalars().first()
    assert user_group is not None, "Default user group should exist."

    user = User.create(
        email=user_payload["email"],
        raw_password=user_payload["password"],
        group_id=user_group.id
    )
    user.is_active = True
    db_session.add(user)
    await db_session.commit()

    login_payload = {
        "email": user_payload["email"],
        "password": user_payload["password"]
    }
    login_response = await client.post("/api/v1/accounts/login/", json=login_payload)
    assert login_response.status_code == 201, "Expected status code 201 for successful login."
    login_data = login_response.json()
    refresh_token = login_data["refresh_token"]

    refresh_payload = {"refresh_token": refresh_token}
    refresh_response = await client.post("/api/v1/accounts/refresh/", json=refresh_payload)
    assert refresh_response.status_code == 200, "Expected status code 200 for successful token refresh."
    refresh_data = refresh_response.json()
    assert "access_token" in refresh_data, "Access token is missing in the response."
    assert refresh_data["access_token"], "Access token is empty."

    access_token_data = jwt_manager.decode_access_token(refresh_data["access_token"])
    assert access_token_data["user_id"] == user.id, "Access token does not contain correct user ID."


@pytest.mark.asyncio
async def test_refresh_access_token_expired_token(client, jwt_manager):
    """
    Test refresh token with expired token.

    Validates that a 400 status code and "Token has expired." message are returned
    when the refresh token is expired.
    """
    expired_token = jwt_manager.create_refresh_token(
        {"user_id": 1},
        expires_delta=timedelta(days=-1)
    )

    refresh_payload = {"refresh_token": expired_token}
    refresh_response = await client.post("/api/v1/accounts/refresh/", json=refresh_payload)

    assert refresh_response.status_code == 400, "Expected status code 400 for expired token."
    assert refresh_response.json()["detail"] == "Token has expired.", "Unexpected error message."


@pytest.mark.asyncio
async def test_refresh_access_token_token_not_found(client, jwt_manager):
    """
    Test refresh token when token is not found in the database.

    Validates that a 401 status code and 'Refresh token not found.' message
    are returned when the refresh token is not stored in the database.
    """
    refresh_token = jwt_manager.create_refresh_token({"user_id": 1})
    refresh_payload = {"refresh_token": refresh_token}
    refresh_response = await client.post("/api/v1/accounts/refresh/", json=refresh_payload)

    assert refresh_response.status_code == 401, "Expected status code 401 for token not found."
    assert refresh_response.json()["detail"] == "Refresh token not found.", "Unexpected error message."


@pytest.mark.asyncio
async def test_refresh_access_token_user_not_found(client, db_session, jwt_manager, seed_user_groups):
    """
    Test refresh token when user ID inside the token does not exist in the database.

    Validates that a 404 status code and "User not found." message
    are returned when the user ID in the token is invalid.

    Steps:
    - Create a new active user.
    - Generate a refresh token with an invalid user ID.
    - Store the refresh token in the database.
    - Attempt to refresh the access token using the invalid refresh token.
    - Verify that the endpoint returns a 404 error with the expected message.
    """
    user_payload = {
        "email": "testuser@example.com",
        "password": "StrongPassword123!"
    }

    stmt = select(UserGroup).where(UserGroup.name == UserGroupEnum.USER)
    result = await db_session.execute(stmt)
    user_group = result.scalars().first()
    assert user_group is not None, "Default user group should exist."

    user = User.create(
        email=user_payload["email"],
        raw_password=user_payload["password"],
        group_id=user_group.id
    )
    user.is_active = True
    db_session.add(user)
    await db_session.commit()

    invalid_user_id = 9999
    refresh_token = jwt_manager.create_refresh_token({"user_id": invalid_user_id})

    refresh_token_record = RefreshToken.create(
        user_id=invalid_user_id,
        days_valid=7,
        token=refresh_token
    )
    db_session.add(refresh_token_record)
    await db_session.commit()

    refresh_payload = {"refresh_token": refresh_token}
    refresh_response = await client.post("/api/v1/accounts/refresh/", json=refresh_payload)

    assert refresh_response.status_code == 404, "Expected status code 404 for non-existent user."
    assert refresh_response.json()["detail"] == "User not found.", "Unexpected error message."


def test_cleanup_expired_activation_tokens_removes_only_expired(tmp_path):
    """
    Verifies that the cleanup task removes only expired activation tokens.

    Setup:

    Points SYNC_DATABASE_URL to a file-based SQLite DB under tmp_path and reloads tasks.tokens so the task binds to this DB.

    Creates a UserGroup, two users, and two ActivationToken rows: one expired (expires_at in the past) and one valid (in the future).

    Action:

    Calls cleanup_expired_activation_tokens() (synchronous Celery task function).

    Assertions:

    The task returns 1 (exactly one row deleted).

    Only the non-expired token remains in the database.

    Notes:

    A file-based SQLite DB (not in-memory) is used to persist state across engine instances and the module reload.
    """
    db_url = f"sqlite:///{tmp_path / 'tokens1.sqlite'}"
    os.environ["SYNC_DATABASE_URL"] = db_url

    token_tasks = importlib.reload(importlib.import_module("tasks.tokens"))

    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)

    SessionLocal = token_tasks.SessionLocal
    with SessionLocal() as s:
        g = UserGroup(name=UserGroupEnum.USER)
        s.add(g); s.flush()

        now = datetime.now(timezone.utc)
        u1 = User(email="expired@example.com", _hashed_password="x", is_active=False, group_id=g.id)
        u2 = User(email="valid@example.com",   _hashed_password="x", is_active=False, group_id=g.id)
        s.add_all([u1, u2]); s.flush()

        s.add_all([
            ActivationToken(user_id=u1.id, token="exp", expires_at=now - timedelta(hours=1)),
            ActivationToken(user_id=u2.id, token="ok",  expires_at=now + timedelta(hours=1)),
        ])
        s.commit()

    deleted = token_tasks.cleanup_expired_activation_tokens()
    assert deleted == 1

    with SessionLocal() as s:
        tokens = s.query(ActivationToken).all()
        assert {t.token for t in tokens} == {"ok"}


def test_cleanup_is_idempotent_when_no_expired(tmp_path):
    """
    Ensures the cleanup task is idempotent when there are no expired tokens.

    Setup:

    Points SYNC_DATABASE_URL to a file-based SQLite DB under tmp_path and reloads tasks.tokens so the task binds to this DB.

    Creates the schema via Base.metadata.create_all without inserting any ActivationToken rows.

    Action:

    Calls cleanup_expired_activation_tokens().

    Assertions:

    The task returns 0 (nothing to delete), confirming no side effects when the dataset has no expired tokens.

    Notes:

    A file-based SQLite DB is used so the reloaded module and engine operate on the same persistent database file.
    """
    db_url = f"sqlite:///{tmp_path / 'tokens2.sqlite'}"
    os.environ["SYNC_DATABASE_URL"] = db_url

    token_tasks = importlib.reload(importlib.import_module("tasks.tokens"))

    engine = create_engine(db_url, future=True)
    Base.metadata.create_all(engine)

    assert token_tasks.cleanup_expired_activation_tokens() == 0
