"""Authentication middleware."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.services.jwt_service import InvalidTokenError, decode_access_token


def _is_public_path(path: str, public_prefixes: Iterable[str]) -> bool:
    return any(path == prefix or path.startswith(prefix) for prefix in public_prefixes)


def get_token_payload(request: Request) -> Mapping[str, Any]:
    payload = getattr(request.state, "token_payload", None)
    if not isinstance(payload, Mapping):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing token payload",
        )
    return payload


def require_super_admin(request: Request) -> Mapping[str, Any]:
    payload = get_token_payload(request)
    if payload.get("is_super_admin") is not True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required",
        )
    return payload


def get_current_tenant_id(request: Request) -> int:
    payload = get_token_payload(request)
    tenant_id = payload.get("tenant_id")
    if isinstance(tenant_id, int) and tenant_id > 0:
        return tenant_id

    tenant_ids = payload.get("tenant_ids")
    if (
        isinstance(tenant_ids, list)
        and len(tenant_ids) == 1
        and isinstance(tenant_ids[0], int)
        and tenant_ids[0] > 0
    ):
        return tenant_ids[0]

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Tenant context required",
    )


def apply_auth_middleware(app: FastAPI) -> None:
    public_prefixes = (
        "/health",
        "/api/v1/ping",
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
