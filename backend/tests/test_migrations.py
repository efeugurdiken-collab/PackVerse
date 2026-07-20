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
P7_REVISION = "d4e6b9a3f1c7"
P8_REVISION = "b7f3e9a1c5d2"  # head as of Sprint P8
P9C2_REVISION = "d657afc740be"  # head as of Sprint P9C2
P10B1_REVISION = "ad3f998eece8"
P10B2_REVISION = "e4ba9bdd172a"
P10B3_REVISION = "cc4808800645"  # head as of Sprint P10B3

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
    "worker_heartbeats",
    "document_chunks",
}

# Columns Sprint P8's migration adds to the pre-existing (P2-era, always
# empty until this sprint) `jobs` table - used by the downgrade-to-P7
# test below to confirm they're removed cleanly, without needing to drop
# and recreate the whole table (which stays present pre- and post-P8).
P8_JOBS_COLUMNS = {
    "target_run_id",
    "error_code",
    "attempt_count",
    "max_attempts",
    "next_attempt_at",
    "lease_expires_at",
    "heartbeat_at",
    "worker_id",
    "cancel_requested_at",
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
    P2/P3/P4/P5/P6/P7/P8 reports cite these exact revision ids as the
    schema history, and each sprint's downgrade-to-previous test depends
    on the down_revision chain staying intact."""
    versions_dir = BACKEND_ROOT / "alembic" / "versions"
    for revision in (
        P2_REVISION,
        P3_REVISION,
        P4_REVISION,
        P5_REVISION,
        P6_REVISION,
        P7_REVISION,
        P8_REVISION,
        P9C2_REVISION,
        P10B1_REVISION,
        P10B2_REVISION,
        P10B3_REVISION,
    ):
        matches = list(versions_dir.glob(f"{revision}_*.py"))
        assert len(matches) == 1, f"expected exactly one migration file for {revision}"


def test_migration_downgrade_to_p2_preserves_domain_tables(test_sync_database_url: str) -> None:
    """P3's users, P5's llm_requests, P6's agent_runs, P7's
    workflow_runs/workflow_step_runs, and P8's worker_heartbeats tables
    must all drop on downgrade to P2 while every P2 domain table
    survives untouched - this is what 'downgrade cleanly back to P2'
    means operationally, not just 'the command exits 0'. None of these
    existed at P2, so all must be excluded from the post-downgrade
    expectation - EXPECTED_TABLES itself always reflects the current
    head and must not be used unmodified as the expected state at an
    earlier revision."""
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
        assert "worker_heartbeats" not in table_names
        assert (
            EXPECTED_TABLES
            - {
                "users",
                "llm_requests",
                "agent_runs",
                "workflow_runs",
                "workflow_step_runs",
                "worker_heartbeats",
                "document_chunks",
            }
            <= table_names
        )
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_to_p4_removes_only_p4_columns(
    test_sync_database_url: str,
) -> None:
    """Downgrading from head (P8) to the explicit P4_REVISION target
    stops right after P4's own migration - P4 stays fully applied, only
    Sprint P5's (llm_requests), P6's (agent_runs), P7's
    (workflow_runs/workflow_step_runs), and P8's (worker_heartbeats)
    additions are undone. So the P4 columns on assets, and the P3 users
    table, must all still be present; llm_requests, agent_runs,
    workflow_runs, workflow_step_runs, and worker_heartbeats must all be
    gone. Uses the explicit P4_REVISION target (not "-1") since head no
    longer *is* P4 - see test_migration_downgrade_one_step_from_head_
    removes_only_p8_additions for the "-1 from current head" case."""
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
        # present; P5's llm_requests, P6's agent_runs, P7's
        # workflow_runs/workflow_step_runs, and P8's worker_heartbeats
        # must not.
        removed = {
            "llm_requests",
            "agent_runs",
            "workflow_runs",
            "workflow_step_runs",
            "worker_heartbeats",
            "document_chunks",
        }
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
    """Downgrading from head (P8) to the explicit P5_REVISION target
    stops right after P5's own migration - P5 stays fully applied
    (llm_requests present), while Sprint P6's addition (agent_runs),
    Sprint P7's additions (workflow_runs, workflow_step_runs), and
    Sprint P8's addition (worker_heartbeats) are all undone. Every P1-P5
    table/column, including P4's asset storage columns, must survive
    untouched."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P5_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        asset_columns = {col["name"] for col in inspector.get_columns("assets")}
        engine.dispose()

        removed = {
            "agent_runs", "workflow_runs", "workflow_step_runs", "worker_heartbeats",
            "document_chunks",
        }
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
    """Downgrading from head (P8) to the explicit P6_REVISION target
    stops right after P6's own migration - P6 stays fully applied
    (agent_runs present), while Sprint P7's additions (workflow_runs,
    workflow_step_runs) and Sprint P8's addition (worker_heartbeats) are
    undone. Every P1-P6 table/column, including P5's llm_requests and
    P4's asset storage columns, must survive untouched."""
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
        assert "worker_heartbeats" not in table_names
        assert "agent_runs" in table_names
        assert "llm_requests" in table_names
        removed = {"workflow_runs", "workflow_step_runs", "worker_heartbeats", "document_chunks"}
        assert EXPECTED_TABLES - removed <= table_names

        p4_columns = {
            "original_filename", "content_type", "etag", "storage_backend",
            "status", "uploaded_by_user_id", "deleted_at",
        }
        assert p4_columns <= asset_columns

        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_to_p7_removes_only_p8_additions(
    test_sync_database_url: str,
) -> None:
    """Downgrading from head (P8) to the explicit P7_REVISION target
    stops right after P7's own migration - P7 stays fully applied
    (workflow_runs/workflow_step_runs present), only Sprint P8's own
    additions are undone: the worker_heartbeats table drops entirely,
    and the nine columns P8 added to the pre-existing (P2-era) `jobs`
    table are removed, while `jobs` itself (and every other P1-P7
    table/column) survives - it was never dropped, only extended."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P7_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        jobs_columns = {col["name"] for col in inspector.get_columns("jobs")}
        engine.dispose()

        assert "worker_heartbeats" not in table_names
        assert "jobs" in table_names
        assert "workflow_runs" in table_names
        assert "workflow_step_runs" in table_names
        assert EXPECTED_TABLES - {"worker_heartbeats", "document_chunks"} <= table_names

        assert P8_JOBS_COLUMNS.isdisjoint(jobs_columns)
        pre_p8_jobs_columns = {
            "id", "job_type", "status", "input_json", "output_json",
            "error_message", "created_at", "updated_at",
        }
        assert pre_p8_jobs_columns <= jobs_columns

        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_to_p9c2_removes_tool_calls_json(
    test_sync_database_url: str,
) -> None:
    """Downgrading from head (P10B1) to the explicit P9C2_REVISION target
    stops right after P9C2's own migration - P9C2 stays fully applied,
    only P10B1's own additions (document_chunks, the vector extension)
    are undone. tool_calls_json (P9C2's own addition on top of P8) must
    still be present; every other agent_runs column, and every P1-P8
    table including worker_heartbeats and the P8 jobs columns, must
    survive untouched."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P9C2_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        agent_run_columns = {col["name"] for col in inspector.get_columns("agent_runs")}
        jobs_columns = {col["name"] for col in inspector.get_columns("jobs")}
        engine.dispose()

        assert "document_chunks" not in table_names
        assert "tool_calls_json" in agent_run_columns
        other_agent_run_columns = {
            "id", "agent_id", "created_by_user_id", "status", "llm_request_id",
            "provider", "model", "input_tokens", "output_tokens", "total_tokens",
            "estimated_cost_usd", "output_text", "error_code", "error_message",
            "duration_ms", "started_at", "completed_at", "created_at", "updated_at",
        }
        assert other_agent_run_columns <= agent_run_columns
        assert "worker_heartbeats" in table_names
        assert P8_JOBS_COLUMNS <= jobs_columns
        assert EXPECTED_TABLES - {"document_chunks"} <= table_names

        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_to_p10b1_removes_embedding_columns(
    test_sync_database_url: str,
) -> None:
    """Downgrading from head (P10B2) to the explicit P10B1_REVISION
    target stops right after P10B1's own migration - P10B1 stays fully
    applied (document_chunks and the vector extension present), only
    P10B2's own additions (the embedding/embedding_model/
    embedding_provider columns) are undone. Every P1-P9C2 table/column,
    including agent_runs' tool_calls_json, worker_heartbeats, and the P8
    jobs columns, must survive untouched."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P10B1_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        agent_run_columns = {col["name"] for col in inspector.get_columns("agent_runs")}
        jobs_columns = {col["name"] for col in inspector.get_columns("jobs")}
        document_chunk_columns = {col["name"] for col in inspector.get_columns("document_chunks")}
        with engine.connect() as conn:
            extension_exists = conn.execute(
                sa.text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
            ).first()
        engine.dispose()

        assert "document_chunks" in table_names
        assert extension_exists is not None
        assert {"embedding", "embedding_model", "embedding_provider"}.isdisjoint(
            document_chunk_columns
        )
        pre_p10b2_document_chunk_columns = {
            "id", "asset_id", "chunk_index", "content", "content_hash",
            "char_start", "char_end", "created_at", "updated_at",
        }
        assert pre_p10b2_document_chunk_columns <= document_chunk_columns
        assert "tool_calls_json" in agent_run_columns
        assert "worker_heartbeats" in table_names
        assert P8_JOBS_COLUMNS <= jobs_columns
        assert EXPECTED_TABLES <= table_names

        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_to_p10b2_removes_only_p10b3_additions(
    test_sync_database_url: str,
) -> None:
    """Downgrading from head (P10B3) to the explicit P10B2_REVISION
    target stops right after P10B2's own migration - P10B2 stays fully
    applied (the embedding/embedding_model/embedding_provider columns
    present), only P10B3's own addition (the
    uq_jobs_active_asset_ingestion partial unique index) is undone.
    Every P1-P10B2 table/column must survive untouched."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, P10B2_REVISION)

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        document_chunk_columns = {col["name"] for col in inspector.get_columns("document_chunks")}
        jobs_index_names = {idx["name"] for idx in inspector.get_indexes("jobs")}
        engine.dispose()

        assert "uq_jobs_active_asset_ingestion" not in jobs_index_names
        assert {"embedding", "embedding_model", "embedding_provider"} <= document_chunk_columns
        assert EXPECTED_TABLES <= table_names

        command.upgrade(cfg, "head")
    finally:
        command.downgrade(cfg, "base")


def test_migration_downgrade_one_step_from_head_removes_only_p10b3_additions(
    test_sync_database_url: str,
) -> None:
    """`alembic downgrade -1` from the true current head (P10B3) must
    remove exactly the uq_jobs_active_asset_ingestion partial unique
    index, and nothing else - the jobs table itself, its P8 columns,
    document_chunks' P10B2 embedding columns, and every other P1-P10B2
    table/column must survive untouched. Re-upgrading to head must
    recreate the index cleanly."""
    cfg = _alembic_config(test_sync_database_url)
    command.upgrade(cfg, "head")
    try:
        command.downgrade(cfg, "-1")

        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        table_names = set(inspector.get_table_names())
        jobs_columns = {col["name"] for col in inspector.get_columns("jobs")}
        jobs_index_names = {idx["name"] for idx in inspector.get_indexes("jobs")}
        document_chunk_columns = {col["name"] for col in inspector.get_columns("document_chunks")}
        engine.dispose()

        assert "uq_jobs_active_asset_ingestion" not in jobs_index_names
        assert "jobs" in table_names
        assert P8_JOBS_COLUMNS <= jobs_columns
        assert {"embedding", "embedding_model", "embedding_provider"} <= document_chunk_columns
        assert EXPECTED_TABLES <= table_names

        command.upgrade(cfg, "head")
        engine = sa.create_engine(test_sync_database_url)
        inspector = sa.inspect(engine)
        jobs_index_names = {idx["name"] for idx in inspector.get_indexes("jobs")}
        engine.dispose()
        assert "uq_jobs_active_asset_ingestion" in jobs_index_names
    finally:
        command.downgrade(cfg, "base")
