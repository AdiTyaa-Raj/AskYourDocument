from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.config.db import get_db, is_db_configured
from app.middleware.auth import get_token_payload
from app.models.access_control import Role, User

router = APIRouter()
logger = logging.getLogger(__name__)


class RoleSummary(BaseModel):
    id: int
    tenant_id: int
    name: str
    description: Optional[str]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RoleListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    roles: List[RoleSummary]


@router.get("/roles", tags=["roles"], response_model=RoleListResponse)
def list_roles(
    request: Request,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> RoleListResponse:
    if not is_db_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    payload = get_token_payload(request)
    is_super_admin = payload.get("is_super_admin", False)

    query = db.query(Role)

    if not is_super_admin:
        email = payload.get("sub") or payload.get("email")
        user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
        if not user or not user.tenant_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User tenant not found")
        query = query.filter(Role.tenant_id == user.tenant_id)

    total = query.count()
    roles = (
        query.order_by(Role.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return RoleListResponse(
        total=total,
        skip=skip,
        limit=limit,
        roles=[RoleSummary.model_validate(r) for r in roles],
    )
