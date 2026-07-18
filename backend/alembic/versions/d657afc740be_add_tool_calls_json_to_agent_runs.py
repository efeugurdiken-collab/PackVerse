"""add tool_calls_json to agent_runs (Sprint P9C2)

Revision ID: d657afc740be
Revises: b7f3e9a1c5d2
Create Date: 2026-07-18 13:48:26.038257

Adds one nullable JSONB column to the existing agent_runs table (Sprint
P6) to persist the per-tool-call trace produced by app/runtime/executor.py's
bounded MCP tool-call loop (Sprint P9C1/P9C2) - see that module's
_run_tool_loop for the exact shape written here. Does not touch any
other table, and does not edit any prior migration file. No
server_default: absent means "no tool calls were made this run", the
same state every pre-P9C2 run - and every run whose agent has no
mcp_server configured - already has by construction.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d657afc740be"
down_revision: Union[str, None] = "b7f3e9a1c5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column("tool_calls_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_runs", "tool_calls_json")
