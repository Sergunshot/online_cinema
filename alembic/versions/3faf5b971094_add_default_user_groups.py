"""add default user groups

Revision ID: 3faf5b971094
Revises: 74626285121c
Create Date: 2025-08-05 12:01:32.017452

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import table, column, Enum

from database import UserGroupEnum

# revision identifiers, used by Alembic.
revision: str = '3faf5b971094'
down_revision: Union[str, Sequence[str], None] = '74626285121c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    user_groups_table = table(
        "user_groups",
        column("name", Enum(UserGroupEnum)),
    )
    op.bulk_insert(
        user_groups_table,
        [{"name": "user"}, {"name": "moderator"}, {"name": "admin"}],
    )


def downgrade() -> None:
    """Downgrade schema."""
    pass
