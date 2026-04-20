"""Chat endpoint – RAG-based Q&A over tenant-scoped documents."""

from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config.db import get_db, is_db_configured
from app.middleware.auth import get_current_tenant_id, get_token_payload
from app.models.access_control import Tenant, User
from app.services.rag_chat_service import chat as rag_chat

router = APIRouter()
logger = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000, description="User question")
    top_k: int = Field(default=5, ge=1, le=20, description="Number of chunks to retrieve")
    tenant_id: Optional[int] = Field(
        default=None,
        gt=0,
        description="Tenant context (required for super admins with multiple tenants)",
    )


class SourceInfo(BaseModel):
    document_id: int
    filename: Optional[str] = None
    similarity: float


class ChatResponse(BaseModel):
    answer: str
    sources: List[SourceInfo]
    chunks_retrieved: int


@router.post("/chat", tags=["chat"], response_model=ChatResponse)
def ask_documents(
    request: Request,
    body: ChatRequest,
    db: Session = Depends(get_db),
) -> ChatResponse:
    """Ask a question and get an answer grounded in your documents.

    Non-super-admin users are always scoped to their tenant (from the DB user record).
    Super admins can either:
      - Provide a tenant context (body.tenant_id or X-Tenant-ID) to scope to that tenant, or
      - Omit tenant context to search only "global" (NULL-tenant) documents.
    """
    if not is_db_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    payload = get_token_payload(request)
    is_super_admin = payload.get("is_super_admin", False)

    tenant_id: Optional[int] = None
    if is_super_admin:
        if body.tenant_id is not None:
            tenant_id = body.tenant_id
        else:
            tenant_header = request.headers.get("X-Tenant-ID")
            if tenant_header:
                try:
                    tenant_id = int(tenant_header)
                except ValueError as exc:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Invalid X-Tenant-ID header; expected an integer tenant id",
                    ) from exc
        # If a tenant context isn't provided, fall back to token-provided tenant context
        # when unambiguous (tenant_id claim or a single tenant_ids entry). Otherwise,
        # keep tenant_id as None to search only global (NULL-tenant) documents.
        if tenant_id is None:
            try:
                tenant_id = get_current_tenant_id(request)
            except HTTPException as exc:
                if exc.status_code != status.HTTP_400_BAD_REQUEST:
                    raise

        tenant_ids = payload.get("tenant_ids")
        if (
            tenant_id is not None
            and isinstance(tenant_ids, list)
            and tenant_ids
            and tenant_id not in tenant_ids
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tenant not authorized",
            )

    if is_super_admin:
        if tenant_id is not None:
            tenant = (
                db.query(Tenant)
                .filter(Tenant.id == tenant_id, Tenant.is_active.is_(True))
                .first()
            )
            if not tenant:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Tenant not found",
                )
    else:
        email = payload.get("sub") or payload.get("email")
        user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
        if not user or not user.tenant_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User tenant not found",
            )
        tenant_id = user.tenant_id

    try:
        result = rag_chat(
            db=db,
            tenant_id=tenant_id,
            user_query=body.query,
            top_k=body.top_k,
        )
    except RuntimeError as exc:
        logger.error("[CHAT] RAG pipeline error for tenant_id=%s: %s", tenant_id, exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return ChatResponse(**result)
