from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session
from typing import Annotated

from app.config.security import AUTH_PASSWORD, AUTH_USERNAME
from app.config.db import get_db, is_db_configured
from app.models.access_control import Role, User, UserRole
from app.services.jwt_service import create_access_token
from app.utils.passwords import verify_password

router = APIRouter()
logger = logging.getLogger(__name__)


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    identifier: Annotated[str, Field(min_length=1, validation_alias=AliasChoices("email", "username"))]
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    email: str
    name: str


@router.post("/login", tags=["auth"], response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> LoginResponse:
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
