"""add orders"""

import sqlalchemy as sa
from alembic import op

revision = "002"
down_revision = "001"


def upgrade():
    op.create_table("orders", sa.Column("id", sa.Integer, primary_key=True))


def downgrade():
    op.drop_table("orders")
