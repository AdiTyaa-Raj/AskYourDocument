from __future__ import annotations

import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.security import AUTH_PASSWORD, AUTH_USERNAME
from app.config.db import get_db, is_db_configured
from app.middleware.auth import get_token_payload, require_super_admin
from app.models.access_control import Role, Tenant, User, UserRole
from app.services.jwt_service import create_access_token
from app.utils.passwords import hash_password, verify_password

router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    identifier: str = Field(min_length=1, validation_alias=AliasChoices("email", "username"))
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    email: str
    name: str


@router.post("/login", tags=["auth"], response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
    # Require a valid access token (handled by middleware); this is a guardrail in case
    # the route is ever made public again.
    get_token_payload(request)

    identifier = payload.identifier.strip()

    if is_db_configured():
        try:
            users = db.query(User).filter(User.email == identifier, User.is_active.is_(True)).all()
            if users:
                if not verify_password(payload.password, users[0].password_hash):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
                    )

                tenant_role_names = [
                    role_name
                    for (role_name,) in (
                        db.query(Role.name)
                        .join(UserRole, UserRole.role_id == Role.id)
                        .filter(UserRole.user_id == users[0].id)
                        .distinct()
                        .all()
                    )
                ]

                super_admin_tenant_ids = [
                    tenant_id
                    for (tenant_id,) in (
                        db.query(User.tenant_id)
                        .join(UserRole, UserRole.user_id == User.id)
                        .join(Role, Role.id == UserRole.role_id)
                        .filter(User.email == identifier, Role.name == "super_admin")
                        .distinct()
                        .all()
                    )
                ]

                role = (
                    "super_admin"
                    if super_admin_tenant_ids
                    else (tenant_role_names[0] if tenant_role_names else "user")
                )

                is_super_admin = bool(super_admin_tenant_ids)
                extra_claims = {
                    "email": identifier,
                    "is_super_admin": is_super_admin,
                    "tenant_ids": super_admin_tenant_ids,
                }
                if not is_super_admin:
                    extra_claims["tenant_id"] = users[0].tenant_id

                return LoginResponse(
                    access_token=create_access_token(
                        subject=identifier,
                        extra_claims=extra_claims,
                    ),
                    role=role,
                    email=users[0].email,
                    name=users[0].full_name,
                )
        except HTTPException:
            raise
        except Exception:
            # If the DB is down / not migrated yet, fall back to env-based auth.
            pass

    if identifier != AUTH_USERNAME or payload.password != AUTH_PASSWORD:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return LoginResponse(
        access_token=create_access_token(
            subject=identifier,
            extra_claims={"email": identifier, "is_super_admin": True, "tenant_ids": []},
        ),
        role="super_admin",
        email=identifier,
        name=identifier,
    )


@router.get("/ping", tags=["health"])
def ping() -> dict:
    return {"message": "pong"}


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


_SLUG_ALLOWED = re.compile(r"[^a-z0-9]+")


def _slugify(value: str) -> str:
    slug = _SLUG_ALLOWED.sub("-", value.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug


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
