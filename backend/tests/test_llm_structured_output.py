"""Tests for structured-output JSON Schema validation (Sprint P5).

app.llm.gateway's _validate_structured_output runs after every provider
call regardless of which provider served it, so exercising it against
FakeProvider (with a controlled response_content) covers the same code
path real Anthropic/OpenAI-compatible responses go through.
"""
from __future__ import annotations

import uuid

import pytest

from app.core.config import Settings
from app.llm.exceptions import LLMStructuredOutputError
from app.llm.gateway import LLMGateway
from app.llm.models import LLMRequest, Message, ResponseFormat
from app.llm.providers.fake import FakeProvider
from app.schemas.llm import ResponseFormatIn
from app.services.llm_service import _to_response_format

_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def _settings(**overrides: object) -> Settings:
    return Settings(jwt_secret_key="x" * 32, llm_allowed_providers="fake", **overrides)


def _request(*, response_format: ResponseFormat | None = None) -> LLMRequest:
    return LLMRequest(
        request_id=str(uuid.uuid4()),
        model="fake-v1",
        messages=(Message(role="user", content="hi"),),
        provider="fake",
        response_format=response_format,
    )


async def test_valid_json_result_passes_through() -> None:
    provider = FakeProvider(response_content='{"answer": "42"}')
    gateway = LLMGateway({"fake": provider}, _settings())

    response = await gateway.generate(
        _request(response_format=ResponseFormat(json_schema=_SCHEMA))
    )

    assert response.content == '{"answer": "42"}'


async def test_malformed_json_raises_structured_output_error() -> None:
    provider = FakeProvider(response_content="not json at all")
    gateway = LLMGateway({"fake": provider}, _settings())

    with pytest.raises(LLMStructuredOutputError) as excinfo:
        await gateway.generate(_request(response_format=ResponseFormat(json_schema=_SCHEMA)))
    assert excinfo.value.raw_text == "not json at all"


async def test_schema_validation_failure_raises_structured_output_error() -> None:
    # Valid JSON, but missing the required "answer" property.
    provider = FakeProvider(response_content='{"wrong_key": "42"}')
    gateway = LLMGateway({"fake": provider}, _settings())

    with pytest.raises(LLMStructuredOutputError):
        await gateway.generate(_request(response_format=ResponseFormat(json_schema=_SCHEMA)))


async def test_no_response_format_skips_validation_entirely() -> None:
    provider = FakeProvider(response_content="plain text, not even JSON")
    gateway = LLMGateway({"fake": provider}, _settings())

    response = await gateway.generate(_request(response_format=None))

    assert response.content == "plain text, not even JSON"


async def test_raw_text_never_appears_in_the_exceptions_own_message() -> None:
    """raw_text is attached as an attribute for internal/debug use only -
    str(exc) (what a naive log line would use) must not embed the full
    raw response, per "do not leak raw responses in production errors"."""
    telltale_text = "TELLTALE-MARKER not valid json"
    provider = FakeProvider(response_content=telltale_text)
    gateway = LLMGateway({"fake": provider}, _settings())

    with pytest.raises(LLMStructuredOutputError) as excinfo:
        await gateway.generate(_request(response_format=ResponseFormat(json_schema=_SCHEMA)))

    assert telltale_text not in str(excinfo.value)
    assert excinfo.value.raw_text == telltale_text


def test_unsupported_structured_output_mode_raises_structured_output_error() -> None:
    with pytest.raises(LLMStructuredOutputError, match="[Uu]nsupported structured output mode"):
        _to_response_format(ResponseFormatIn(mode="xml_schema", json_schema=_SCHEMA))
