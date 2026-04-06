from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


@router.get("/ping", tags=["health"])
def ping() -> dict:
    return {"message": "pong"}
