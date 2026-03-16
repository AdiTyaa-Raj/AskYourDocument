from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.config.security import AUTH_PASSWORD, AUTH_USERNAME
from app.config.db import get_db, is_db_configured
from app.models.access_control import Role, User, UserRole
from app.services.jwt_service import create_access_token
from app.utils.passwords import verify_password

router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    identifier: str = Field(min_length=1, validation_alias=AliasChoices("email", "username"))
    password: str = Field(min_length=1)


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", tags=["auth"], response_model=LoginResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> LoginResponse:
    identifier = payload.identifier.strip()

    if is_db_configured():
        try:
            users = db.query(User).filter(User.email == identifier, User.is_active.is_(True)).all()
            if users:
                if not verify_password(payload.password, users[0].password_hash):
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
                    )

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

                return LoginResponse(
                    access_token=create_access_token(
                        subject=identifier,
                        extra_claims={
                            "email": identifier,
                            "is_super_admin": bool(super_admin_tenant_ids),
                            "tenant_ids": super_admin_tenant_ids,
                        },
                    )
                )
        except HTTPException:
            raise
        except Exception:
            # If the DB is down / not migrated yet, fall back to env-based auth.
            pass

    if identifier != AUTH_USERNAME or payload.password != AUTH_PASSWORD:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    return LoginResponse(access_token=create_access_token(subject=identifier))


@router.get("/ping", tags=["health"])
def ping() -> dict:
    return {"message": "pong"}
