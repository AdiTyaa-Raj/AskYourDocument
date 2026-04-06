from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.db import get_db, is_db_configured
from app.middleware.auth import require_super_admin
from app.models.access_control import Tenant

router = APIRouter()
logger = logging.getLogger(__name__)

_SLUG_ALLOWED = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_ALLOWED.sub("-", value.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


class TenantSummary(BaseModel):
    id: int
    name: str
    slug: str
    superuser_name: Optional[str]
    superuser_email: Optional[EmailStr]
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TenantListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    tenants: List[TenantSummary]


class CreateTenantRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: Optional[str] = Field(default=None, min_length=1, max_length=100)
    superuser_name: Optional[str] = Field(default=None, max_length=255)
    superuser_email: Optional[EmailStr] = None
    is_active: bool = True


class CreateTenantResponse(BaseModel):
    id: int
    name: str
    slug: str
    superuser_name: Optional[str]
    superuser_email: Optional[EmailStr]
    is_active: bool


@router.get("/tenants", tags=["tenants"], response_model=TenantListResponse)
def list_tenants(
    request: Request,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> TenantListResponse:
    require_super_admin(request)

    if not is_db_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    query = db.query(Tenant)
    total = query.count()
    tenants = (
        query.order_by(Tenant.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return TenantListResponse(
        total=total,
        skip=skip,
        limit=limit,
        tenants=[TenantSummary.model_validate(t) for t in tenants],
    )


@router.post(
    "/tenants",
    tags=["tenants"],
    status_code=status.HTTP_201_CREATED,
    response_model=CreateTenantResponse,
)
def create_tenant(
    payload: CreateTenantRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> CreateTenantResponse:
    require_super_admin(request)

    if not is_db_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    name = payload.name.strip()
    slug = (payload.slug.strip().lower() if payload.slug else _slugify(name))
    if not slug:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid slug")

    tenant = Tenant(
        name=name,
        slug=slug,
        superuser_name=payload.superuser_name.strip() if payload.superuser_name else None,
        superuser_email=str(payload.superuser_email).strip().lower()
        if payload.superuser_email
        else None,
        is_active=payload.is_active,
    )

    try:
        db.add(tenant)
        db.commit()
        db.refresh(tenant)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tenant slug already exists",
        ) from exc

    return CreateTenantResponse(
        id=tenant.id,
        name=tenant.name,
        slug=tenant.slug,
        superuser_name=tenant.superuser_name,
        superuser_email=tenant.superuser_email,
        is_active=tenant.is_active,
    )
