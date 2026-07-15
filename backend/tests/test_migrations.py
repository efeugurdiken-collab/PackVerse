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
P3_REVISION = "1f20f57819a3"  # head as of Sprint P3

EXPECTED_TABLES = {
    "products",
    "assets",
    "jobs",
    "agent_definitions",
    "workflow_definitions",
    "users",
}


def _alembic_config(sync_url: str) -> Config:
    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    cfg.set_main_option("sqlalchemy.url", sync_url)
    return cfg


def test_migration_upgrade_creates_all_six_tables(test_sync_database_url: str) -> None:
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
    """Guards against silently renumbering either migration - the Sprint
    P2 and P3 reports cite these exact revision ids as the schema
    history, and P3 downgrading cleanly back to P2 depends on this
    down_revision chain staying intact."""
    versions_dir = BACKEND_ROOT / "alembic" / "versions"
    for revision in (P2_REVISION, P3_REVISION):
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
