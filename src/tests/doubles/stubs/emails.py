from decimal import Decimal

from notifications import EmailSenderInterface


class StubEmailSender(EmailSenderInterface):

    async def send_activation_email(self, email: str, activation_link: str) -> None:
        """
        Stub implementation for sending an activation email.

        Args:
            email (str): The recipient's email address.
            activation_link (str): The activation link to include in the email.
        """
        return None

    async def send_activation_complete_email(self, email: str, login_link: str) -> None:
        """
        Stub implementation for sending an account activation complete email.

        Args:
            email (str): The recipient's email address.
            login_link (str): The login link to include in the email.
        """
        return None

    async def send_password_reset_email(self, email: str, reset_link: str) -> None:
        """
        Stub implementation for sending a password reset email.

        Args:
            email (str): The recipient's email address.
            reset_link (str): The password reset link to include in the email.
        """
        return None

    async def send_password_reset_complete_email(self, email: str, login_link: str) -> None:
        """
        Stub implementation for sending a password reset complete email.

        Args:
            email (str): The recipient's email address.
            login_link (str): The login link to include in the email.
        """
        return None

    async def send_password_change(self, email: str) -> None:
        return None

    async def send_remove_movie(
            self, email: str, movie_name: str, cart_id: int
    ) -> None:
        return None

    async def send_comment_answer(self, email: str, answer_text: str) -> None:
        return None

    async def send_payment_email(self, email: str, amount: Decimal) -> None:
        return None

    async def send_refund_email(self, email: str, amount: Decimal) -> None:
        return None

    async def send_cancellation_email(self, email: str, amount: Decimal) -> None:
        return None
