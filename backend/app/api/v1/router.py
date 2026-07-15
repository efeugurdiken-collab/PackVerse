"""Aggregates all v1 API routers into a single router mounted by main.py."""
from fastapi import APIRouter

from app.api.v1 import assets, auth, health, llm, products

api_v1_router = APIRouter()
api_v1_router.include_router(health.router)
api_v1_router.include_router(auth.router)
api_v1_router.include_router(products.router)
api_v1_router.include_router(assets.router)
api_v1_router.include_router(llm.router)
