from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.db import get_db, is_db_configured
from app.middleware.auth import get_token_payload, require_super_admin
from app.models.access_control import Role, Tenant, User, UserRole
from app.utils.passwords import hash_password

router = APIRouter()
logger = logging.getLogger(__name__)


class CurrentUserResponse(BaseModel):
    id: int
    email: EmailStr
    full_name: str
    is_active: bool
    tenant_id: Optional[int]
    tenant_name: Optional[str]
    role: str


class CreateUserRequest(BaseModel):
    tenant_id: int = Field(gt=0)
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=8)
    is_active: bool = True
    role: Optional[str] = Field(default=None, min_length=1, max_length=120)


class CreateUserResponse(BaseModel):
    id: int
    tenant_id: int
    email: EmailStr
    full_name: str
    is_active: bool


class UserSummary(BaseModel):
    id: int
    tenant_id: int
    email: EmailStr
    full_name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    users: List[UserSummary]


@router.get("/users/me", tags=["users"], response_model=CurrentUserResponse)
def get_current_user(request: Request, db: Session = Depends(get_db)) -> CurrentUserResponse:
    payload = get_token_payload(request)
    email = payload.get("sub") or payload.get("email")

    if not is_db_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    is_super_admin = payload.get("is_super_admin", False)
    if is_super_admin:
        role = "super_admin"
    else:
        role_name = (
            db.query(Role.name)
            .join(UserRole, UserRole.role_id == Role.id)
            .filter(UserRole.user_id == user.id)
            .scalar()
        )
        role = role_name or "user"

    tenant_name: Optional[str] = None
    if user.tenant_id:
        tenant = db.query(Tenant).filter(Tenant.id == user.tenant_id).first()
        tenant_name = tenant.name if tenant else None

    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        tenant_id=user.tenant_id,
        tenant_name=tenant_name,
        role=role,
    )


@router.post("/users", tags=["users"], status_code=status.HTTP_201_CREATED, response_model=CreateUserResponse)
def create_user(
    payload: CreateUserRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> CreateUserResponse:
    require_super_admin(request)

    if not is_db_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    tenant = db.query(Tenant).filter(Tenant.id == payload.tenant_id, Tenant.is_active.is_(True)).first()
    if not tenant:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    email = payload.email.strip().lower()

    role_id: Optional[int] = None
    if payload.role:
        role = (
            db.query(Role)
            .filter(
                Role.tenant_id == payload.tenant_id,
                Role.name == payload.role,
                Role.is_active.is_(True),
            )
            .first()
        )
        if not role:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Role not found")
        role_id = role.id

    user = User(
        tenant_id=payload.tenant_id,
        email=email,
        full_name=payload.full_name.strip(),
        password_hash=hash_password(payload.password),
        is_active=payload.is_active,
    )

    try:
        db.add(user)
        db.flush()
        if role_id is not None:
            db.add(UserRole(user_id=user.id, role_id=role_id))
        db.commit()
        db.refresh(user)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="User already exists for this tenant",
        ) from exc

    return CreateUserResponse(
        id=user.id,
        tenant_id=user.tenant_id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
    )


@router.get("/users", tags=["users"], response_model=UserListResponse)
def list_users(
    request: Request,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> UserListResponse:
    if not is_db_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    payload = get_token_payload(request)
    email = payload.get("sub") or payload.get("email")
    current_user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    if not current_user or not current_user.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User tenant not found")

    query = db.query(User).filter(User.tenant_id == current_user.tenant_id)

    total = query.count()
    users = (
        query.order_by(User.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return UserListResponse(
        total=total,
        skip=skip,
        limit=limit,
        users=[UserSummary.model_validate(u) for u in users],
    )
