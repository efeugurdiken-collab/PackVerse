"""LLM Gateway (Sprint P5): a provider-agnostic interface for calling
multiple LLM providers through one stable internal API.

Nothing in this package imports FastAPI or SQLAlchemy - see
app/llm/base.py and app/llm/models.py. The HTTP/DB-facing layers live in
app/schemas/llm.py, app/services/llm_service.py, and app/api/v1/llm.py.
"""
