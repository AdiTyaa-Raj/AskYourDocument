"""Authentication middleware."""

from __future__ import annotations

from typing import Iterable

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.services.jwt_service import InvalidTokenError, decode_access_token


def _is_public_path(path: str, public_prefixes: Iterable[str]) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in public_prefixes)


def apply_auth_middleware(app: FastAPI) -> None:
    public_prefixes = (
        "/health",
        "/api/v1/login",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/static",
    )

    @app.middleware("http")
    async def jwt_auth_middleware(request: Request, call_next):
        if request.method == "OPTIONS":
            return await call_next(request)
        if _is_public_path(request.url.path, public_prefixes):
            return await call_next(request)

        auth_header = request.headers.get("Authorization")
        if not auth_header:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing Authorization header"},
            )

        scheme, _, token = auth_header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "Invalid Authorization header; expected Bearer token"},
            )

        try:
            request.state.token_payload = decode_access_token(token)
        except InvalidTokenError as exc:
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": str(exc)}
            )

        return await call_next(request)
