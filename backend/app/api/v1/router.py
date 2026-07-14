"""Aggregates all v1 API routers into a single router mounted by main.py."""
from fastapi import APIRouter

from app.api.v1 import health

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
