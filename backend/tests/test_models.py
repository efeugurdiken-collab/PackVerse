"""Model creation tests for the Sprint P2 domain models.

Each test persists a row via the ORM and re-reads it in a fresh query to
confirm defaults, relationships, and cascade behavior work as declared -
not just that the Python object can be constructed.
"""
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_definition import AgentDefinition
from app.models.asset import Asset
from app.models.enums import (
    AgentStatus,
    JobStatus,
    ProductStatus,
    ProductType,
    WorkflowStatus,
)
from app.models.job import Job
from app.models.product import Product
from app.models.workflow_definition import WorkflowDefinition


async def test_product_creation_applies_defaults(db_session: AsyncSession) -> None:
    product = Product(
        slug="brand-kit-starter",
        title="Brand Kit Starter",
        product_type=ProductType.BRAND_KIT,
        price_cents=1999,
        currency="USD",
    )
    db_session.add(product)
    await db_session.commit()
    await db_session.refresh(product)

    assert isinstance(product.id, uuid.UUID)
    assert product.status == ProductStatus.DRAFT
    assert product.version == "v1.0"
    assert product.metadata_json == {}
    assert product.created_at is not None
    assert product.updated_at is not None


async def test_asset_relationship_and_cascade_delete(db_session: AsyncSession) -> None:
    product = Product(
        slug="svg-pack-icons",
        title="SVG Pack: Icons",
        product_type=ProductType.SVG_PACK,
        price_cents=999,
        currency="USD",
    )
    asset = Asset(
        product=product,
        asset_type="preview",
        filename="icons-preview.svg",
        storage_key=f"assets/{uuid.uuid4()}/icons-preview.svg",
        mime_type="image/svg+xml",
        size_bytes=2048,
        checksum="deadbeef",
    )
    db_session.add(product)
    db_session.add(asset)
    await db_session.commit()

    reloaded = await db_session.get(Product, product.id)
    assert reloaded is not None
    assert len(reloaded.assets) == 1
    assert reloaded.assets[0].filename == "icons-preview.svg"

    await db_session.delete(reloaded)
    await db_session.commit()

    remaining = await db_session.scalar(select(Asset).where(Asset.product_id == product.id))
    assert remaining is None


async def test_asset_storage_key_must_be_unique(db_session: AsyncSession) -> None:
    product = Product(
        slug="texture-pack-concrete",
        title="Texture Pack: Concrete",
        product_type=ProductType.TEXTURE_PACK,
        price_cents=1499,
        currency="USD",
    )
    db_session.add(product)
    await db_session.flush()

    shared_key = f"assets/{uuid.uuid4()}/dup.png"
    db_session.add(
        Asset(
            product_id=product.id,
            asset_type="preview",
            filename="dup.png",
            storage_key=shared_key,
            mime_type="image/png",
            size_bytes=1024,
            checksum="aaa",
        )
    )
    await db_session.commit()

    db_session.add(
        Asset(
            product_id=product.id,
            asset_type="preview",
            filename="dup2.png",
            storage_key=shared_key,
            mime_type="image/png",
            size_bytes=1024,
            checksum="bbb",
        )
    )
    with pytest.raises(Exception):  # sqlalchemy.exc.IntegrityError at the DB level
        await db_session.commit()
    await db_session.rollback()


async def test_job_creation_applies_defaults(db_session: AsyncSession) -> None:
    job = Job(job_type="generate_assets", input_json={"product_id": str(uuid.uuid4())})
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    assert job.status == JobStatus.PENDING
    assert job.output_json is None
    assert job.started_at is None
    assert job.completed_at is None


async def test_agent_definition_name_must_be_unique(db_session: AsyncSession) -> None:
    db_session.add(
        AgentDefinition(
            name="research-agent",
            role="Research Agent",
            configuration_json={"model": "claude"},
        )
    )
    await db_session.commit()

    db_session.add(
        AgentDefinition(name="research-agent", role="Duplicate", configuration_json={})
    )
    with pytest.raises(Exception):  # sqlalchemy.exc.IntegrityError at the DB level
        await db_session.commit()
    await db_session.rollback()


async def test_workflow_definition_creation_applies_defaults(db_session: AsyncSession) -> None:
    workflow = WorkflowDefinition(
        name="create-product",
        definition_json={"steps": ["plan", "generate", "review", "publish"]},
    )
    db_session.add(workflow)
    await db_session.commit()
    await db_session.refresh(workflow)

    assert workflow.status == WorkflowStatus.DRAFT
    assert workflow.version == "v1.0"
    assert AgentStatus.DRAFT.value == "draft"  # sanity check on shared enum shape
