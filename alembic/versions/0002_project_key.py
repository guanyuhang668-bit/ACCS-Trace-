"""add nullable project compatibility key

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-02
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0002"
down_revision: Union[str, Sequence[str], None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("project_key", sa.String(length=255), nullable=True),
    )
    op.create_index(
        op.f("ix_projects_project_key"),
        "projects",
        ["project_key"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_projects_project_key"), table_name="projects")
    op.drop_column("projects", "project_key")
