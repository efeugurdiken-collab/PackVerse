"""Alembic migration verification.

This does NOT run against SQLite or any in-memory substitute: the
migration uses PostgreSQL-only types (JSONB, native `now()`), so it can
only be verified against a real PostgreSQL instance - the same one
pointed to by settings.test_database_url.

This test drives Alembic through its public Python API (rather than
shelling out to `alembic upgrade head` via subprocess) so failures show
up as normal pytest assertions/tracebacks. Alembic's command API and
`sqlalchemy.create_engine`/`inspect` are synchronous by design, so these
test functions are plain `def`, not `async def` - pytest-asyncio's
"auto" mode only affects coroutine functions and leaves these alone.
"""
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

BACKEND_ROOT = Path(__file__).resolve().parent.parent
P2_REVISION = "06b17a0f30ad"
P3_REVISION = "1f20f57819a3"
P4_REVISION = "ae14cc314d2f"
P5_REVISION = "7c19e4b8a2d6"
P6_REVISION = "a1c8f7d2b3e9"
P7_REVISION = "d4e6b9a3f1c7"  # head as of Sprint P7

EXPECTED_TABLES = {
    "products",
    "assets",
    "jobs",
    "agent_definitions",
    "workflow_definitions",
    "users",
    "llm_requests",
    "agent_runs",
    "workflow_runs",
    "workflow_step_runs",
}


def _alembic_config(sync_url: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


def test_migration_upgrade_creates_all_expected_tables(test_sync_database_url: str) -> None:
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        assert EXPECTED_TABLES.issubset(table_names)
        engine.dispose()
    finally:
        command.downgrade(cfg, "base")


def test_migration_upgrade_is_idempotent_and_downgrade_is_clean(
    test_sync_database_url: str,
) -> None:
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = sa.create_engine(test_sync_database_url)
    inspector = sa.inspect(engine)
    remaining = EXPECTED_TABLES.intersection(inspector.get_table_names())
    engine.dispose()

    assert remaining == set(), f"downgrade left tables behind: {remaining}"

    # Re-running upgrade after a clean downgrade must succeed without
    # leftover state from the previous run.
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")


def test_migration_revision_identifiers_match_expected() -> None:
    """Guards against silently renumbering any migration - the Sprint
    P2/P3/P4/P5/P6/P7 reports cite these exact revision ids as the schema
    history, and each sprint's downgrade-to-previous test depends on the
    down_revision chain staying intact."""
    versions_dir = BACKEND_ROOT / "alembic" / "versions"
    for revision in (
        P2_REVISION,
        P3_REVISION,
        P4_REVISION,
        P5_REVISION,
        P6_REVISION,
        P7_REVISION,
    ):
        matches = list(versions_dir.glob(f"{revision}_*.py"))
        assert len(matches) == 1, f"expected exactly one migration file for {revision}"


def test_migration_downgrade_to_p2_preserves_domain_tables(test_sync_database_url: str) -> None:
    """P3's users, P5's llm_requests, P6's agent_runs, and P7's
    workflow_runs/workflow_step_runs tables must all drop on downgrade
    to P2 while every P2 domain table survives untouched - this is what
    'downgrade cleanly back to P2' means operationally, not just 'the
    command exits 0'. None of these existed at P2, so all must be
    excluded from the post-downgrade expectation - EXPECTED_TABLES
    itself always reflects the current head and must not be used
    unmodified as the expected state at an earlier revision."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P2_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        engine.dispose()

        assert "users" not in table_names
        assert "llm_requests" not in table_names
        assert "agent_runs" not in table_names
        assert "workflow_runs" not in table_names
        assert "workflow_step_runs" not in table_names
        assert (
            EXPECTED_TABLES
            - {"users", "llm_requests", "agent_runs", "workflow_runs", "workflow_step_runs"}
            <= table_names
        )
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_to_p4_removes_only_p4_columns(
    test_sync_database_url: str,
) -> None:
    """Downgrading from head (P7) to the explicit P4_REVISION target
    stops right after P4's own migration - P4 stays fully applied, only
    Sprint P5's (llm_requests), P6's (agent_runs), and P7's
    (workflow_runs/workflow_step_runs) additions are undone. So the P4
    columns on assets, and the P3 users table, must all still be
    present; llm_requests, agent_runs, workflow_runs, and
    workflow_step_runs must all be gone. Uses the explicit P4_REVISION
    target (not "-1") since head no longer *is* P4 - see
    test_migration_downgrade_one_step_from_head_removes_only_workflow_
    tables for the "-1 from current head" case."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P4_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        asset_columns = {col["name"] for col in inspector.get_columns("assets")}
        engine.dispose()

        # Every table up through P4, including P3's users, must still be
        # present; P5's llm_requests, P6's agent_runs, and P7's
        # workflow_runs/workflow_step_runs must not.
        removed = {"llm_requests", "agent_runs", "workflow_runs", "workflow_step_runs"}
        assert EXPECTED_TABLES - removed <= table_names
        assert removed.isdisjoint(table_names)

        p4_columns = {
            "original_filename",
            "content_type",
            "etag",
            "storage_backend",
            "status",
            "uploaded_by_user_id",
            "deleted_at",
        }
        # P4 is still fully applied at this target revision - these
        # columns must survive, not disappear.
        assert p4_columns <= asset_columns

        pre_p4_columns = {
            "id", "product_id", "asset_type", "filename", "storage_key",
            "mime_type", "size_bytes", "checksum", "created_at", "updated_at",
        }
        assert pre_p4_columns <= asset_columns

        # And upgrading back to head must succeed cleanly from here -
        # not raising is the assertion; alembic's command functions
        # don't return a meaningful value to inspect.
        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_to_p5_removes_agent_runs_and_workflow_run_tables(
    test_sync_database_url: str,
) -> None:
    """Downgrading from head (P7) to the explicit P5_REVISION target
    stops right after P5's own migration - P5 stays fully applied
    (llm_requests present), while Sprint P6's addition (agent_runs) and
    Sprint P7's additions (workflow_runs, workflow_step_runs) are all
    undone. Every P1-P5 table/column, including P4's asset storage
    columns, must survive untouched."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P5_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        asset_columns = {col["name"] for col in inspector.get_columns("assets")}
        engine.dispose()

        removed = {"agent_runs", "workflow_runs", "workflow_step_runs"}
        assert removed.isdisjoint(table_names)
        assert EXPECTED_TABLES - removed <= table_names

        p4_columns = {
            "original_filename", "content_type", "etag", "storage_backend",
            "status", "uploaded_by_user_id", "deleted_at",
        }
        assert p4_columns <= asset_columns

        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_to_p6_removes_only_workflow_run_tables(
    test_sync_database_url: str,
) -> None:
    """Downgrading from head (P7) to the explicit P6_REVISION target
    stops right after P6's own migration - P6 stays fully applied
    (agent_runs present), only Sprint P7's additions (workflow_runs,
    workflow_step_runs) are undone. Every P1-P6 table/column, including
    P5's llm_requests and P4's asset storage columns, must survive
    untouched."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P6_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        asset_columns = {col["name"] for col in inspector.get_columns("assets")}
        engine.dispose()

        assert "workflow_runs" not in table_names
        assert "workflow_step_runs" not in table_names
        assert "agent_runs" in table_names
        assert "llm_requests" in table_names
        assert EXPECTED_TABLES - {"workflow_runs", "workflow_step_runs"} <= table_names

        p4_columns = {
            "original_filename", "content_type", "etag", "storage_backend",
            "status", "uploaded_by_user_id", "deleted_at",
        }
        assert p4_columns <= asset_columns

        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_one_step_from_head_removes_only_workflow_run_tables(
    test_sync_database_url: str,
) -> None:
    """`alembic downgrade -1` from head (P7) must remove exactly the
    workflow_runs and workflow_step_runs tables (both created by the
    same P7 migration) and nothing else - every P1-P6 table, including
    P6's agent_runs, P5's llm_requests, and P4's asset storage columns,
    must survive untouched."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, "-1")

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        asset_columns = {col["name"] for col in inspector.get_columns("assets")}
        engine.dispose()

        assert "workflow_runs" not in table_names
        assert "workflow_step_runs" not in table_names
        assert "agent_runs" in table_names
        assert "llm_requests" in table_names
        assert EXPECTED_TABLES - {"workflow_runs", "workflow_step_runs"} <= table_names

        p4_columns = {
            "original_filename", "content_type", "etag", "storage_backend",
            "status", "uploaded_by_user_id", "deleted_at",
        }
        assert p4_columns <= asset_columns

        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")
