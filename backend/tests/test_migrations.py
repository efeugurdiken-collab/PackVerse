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
P5_REVISION = "7c19e4b8a2d6"  # head as of Sprint P5

EXPECTED_TABLES = {
    "products",
    "assets",
    "jobs",
    "agent_definitions",
    "workflow_definitions",
    "users",
    "llm_requests",
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
    P2/P3/P4/P5 reports cite these exact revision ids as the schema
    history, and each sprint's downgrade-to-previous test depends on the
    down_revision chain staying intact."""
    versions_dir = BACKEND_ROOT / "alembic" / "versions"
    for revision in (P2_REVISION, P3_REVISION, P4_REVISION, P5_REVISION):
        matches = list(versions_dir.glob(f"{revision}_*.py"))
        assert len(matches) == 1, f"expected exactly one migration file for {revision}"


def test_migration_downgrade_to_p2_preserves_domain_tables(test_sync_database_url: str) -> None:
    """P3's users table must drop on downgrade to P2 while every P2
    domain table survives untouched - this is what 'downgrade cleanly
    back to P2' means operationally, not just 'the command exits 0'."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P2_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        engine.dispose()

        assert "users" not in table_names
        assert EXPECTED_TABLES - {"users"} <= table_names
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_to_p4_removes_only_p4_columns(
    test_sync_database_url: str,
) -> None:
    """Downgrading from head (P5) to P4 must remove exactly the P4
    columns added to assets and nothing else - the P3 users table and
    every column that existed before P4 must survive untouched. Uses the
    explicit P4_REVISION target (not "-1") since head no longer *is* P4
    as of Sprint P5 - see test_migration_downgrade_one_step_from_head_
    removes_only_llm_requests_table for the "-1 from current head" case."""
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
        # present; P5's llm_requests must not.
        assert EXPECTED_TABLES - {"llm_requests"} <= table_names
        assert "llm_requests" not in table_names

        p4_only_columns = {
            "original_filename",
            "content_type",
            "etag",
            "storage_backend",
            "status",
            "uploaded_by_user_id",
            "deleted_at",
        }
        assert asset_columns.isdisjoint(p4_only_columns)

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


def test_migration_downgrade_one_step_from_head_removes_only_llm_requests_table(
    test_sync_database_url: str,
) -> None:
    """`alembic downgrade -1` from head (P5) must remove exactly the
    llm_requests table and nothing else - every P1-P4 table, including
    P4's asset storage columns, must survive untouched."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, "-1")

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        asset_columns = {col["name"] for col in inspector.get_columns("assets")}
        engine.dispose()

        assert "llm_requests" not in table_names
        assert EXPECTED_TABLES - {"llm_requests"} <= table_names

        p4_columns = {
            "original_filename", "content_type", "etag", "storage_backend",
            "status", "uploaded_by_user_id", "deleted_at",
        }
        assert p4_columns <= asset_columns

        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")
