"""Pydantic v2 schemas package.

Kept strictly separate from app/models/ (SQLAlchemy ORM classes) per the
P2 database rules: schemas define the API contract, models define the
storage contract, and the two are allowed to diverge deliberately (e.g.
schemas never expose internal DB-only fields).
"""
