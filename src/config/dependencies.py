import os

from fastapi import Depends, HTTPException
from starlette import status

from config.settings import TestingSettings, Settings, BaseAppSettings, LocalSettings
from exceptions import BaseSecurityError
from notifications import EmailSenderInterface, EmailSender
from security import get_token
from security.interfaces import JWTAuthManagerInterface
from security.token_manager import JWTAuthManager
from storages import S3StorageInterface, S3StorageClient


def get_settings() -> BaseAppSettings:
    """
    Retrieve the application settings based on the current environment.

    This function reads the 'ENVIRONMENT' environment variable (defaulting to 'local' if not set)
    and returns a corresponding settings instance. If the environment is 'testing', it returns an instance
    of TestingSettings; otherwise, it returns an instance of Settings.

    Returns:
        BaseAppSettings: The settings instance appropriate for the current environment.
    """
    env_mode = os.getenv("ENVIRONMENT", "local")
    if env_mode == "testing":
        return TestingSettings()

    if env_mode == "local":
        return LocalSettings()

    return Settings()  # env_mode == "docker" or else


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
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token: user_id missing")
        return user_id
    except BaseSecurityError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(e))
