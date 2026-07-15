"""Shared pytest fixtures.

Test isolation strategy: each test runs inside an outer database
transaction that is rolled back afterward, even though the service layer
itself calls `session.commit()`. This is the standard SQLAlchemy "join a
session into an external transaction" recipe: open a connection + outer
transaction, bind the test's AsyncSession to that connection, and restart
a SAVEPOINT every time application code commits. When the test ends, the
outer transaction is rolled back, discarding everything - regardless of
test execution order.

The schema is created and dropped inside each test's own event loop
(function-scoped, matching pytest-asyncio's default "auto" mode) rather
than a session-scoped engine, to avoid binding an engine/connection to an
event loop that no longer exists by the time a later test runs.

Requires a reachable PostgreSQL instance at settings.test_database_url
(defaults to "<postgres_db>_test"). Create it once, e.g.:
    docker compose exec db createdb -U packverse packverse_test
"""
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.core.security import create_access_token, hash_password
from app.database.session import get_db
from app.main import app
from app.models import Base
from app.models.enums import UserRole, UserStatus
from app.models.user import User

settings = get_settings()


@pytest.fixture
def test_sync_database_url() -> str:
    """The psycopg2 (sync) connection string for the isolated test
    database, used by tests/test_migrations.py to drive Alembic - Alembic's
    programmatic API is sync, independent of the async engine used
    everywhere else in the test suite."""
    return settings.test_sync_database_url


@pytest.fixture
async def test_engine() -> AsyncGenerator[AsyncEngine, None]:
    """Creates the schema for this test's event loop, drops it afterward."""
    engine = create_async_engine(settings.test_database_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield engine
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest.fixture
async def db_session(test_engine: AsyncEngine) -> AsyncGenerator[AsyncSession, None]:
    """A session whose changes are always rolled back after the test,
    even though application code calls commit()."""
    connection = await test_engine.connect()
    outer_transaction = await connection.begin()

    session_factory = async_sessionmaker(bind=connection, expire_on_commit=False)
    session = session_factory()

    # Restart a SAVEPOINT every time the service layer's session.commit()
    # ends the current nested transaction, so subsequent commits keep
    # writing inside a transaction that our outer rollback can still undo.
    nested = await connection.begin_nested()

    @event.listens_for(session.sync_session, "after_transaction_end")
    def _restart_savepoint(sess: object, trans: object) -> None:
        nonlocal nested
        if not connection.closed and not nested.is_active:
            nested = connection.sync_connection.begin_nested()

    try:
        yield session
    finally:
        await session.close()
        await outer_transaction.rollback()
        await connection.close()


@pytest.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """An HTTP client whose requests use the isolated db_session above,
    via a FastAPI dependency override."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(
    db_session: AsyncSession,
) -> Callable[..., Awaitable[User]]:
    """Factory fixture: `await make_user(role=UserRole.OPERATOR)` inserts
    a user directly via the ORM (bypassing the /auth/register endpoint,
    since tests need to construct users in states - e.g. DISABLED - that
    endpoint can never produce) and returns it."""

    async def _make_user(
        *,
        email: str | None = None,
        password: str = "a-perfectly-fine-passw0rd",
        role: UserRole = UserRole.VIEWER,
        status: UserStatus = UserStatus.ACTIVE,
        is_verified: bool = False,
    ) -> User:
        user = User(
            email=email or f"user-{uuid.uuid4().hex[:10]}@example.com",
            hashed_password=hash_password(password),
            full_name="Test User",
            role=role,
            status=status,
            is_verified=is_verified,
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    return _make_user


@pytest.fixture
def auth_headers() -> Callable[[User], dict[str, str]]:
    """`auth_headers(user)` -> a ready-to-use Authorization header for a
    valid access token belonging to that user."""

    def _auth_headers(user: User) -> dict[str, str]:
        token = create_access_token(subject=user.id, role=user.role.value)
        return {"Authorization": f"Bearer {token}"}

    return _auth_headers
