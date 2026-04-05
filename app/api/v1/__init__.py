from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Annotated, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi import File, Form, UploadFile
from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config.security import AUTH_PASSWORD, AUTH_USERNAME
from app.config.db import get_db, is_db_configured
from app.middleware.auth import get_token_payload, require_super_admin
from app.models.access_control import Role, Tenant, User, UserRole
from app.models.document_text_extraction import DocumentTextExtraction
from app.services.document_chunking_service import chunk_and_store
from app.services.document_text_extraction_service import (
    extract_and_store_text_pdfplumber,
    is_pdfplumber_enabled_on_upload,
)
from app.services.jwt_service import create_access_token
from app.services.email_service import BrevoNotConfiguredError, BrevoSendError, send_email
from app.services.s3_storage_service import S3NotConfiguredError, S3UploadError, upload_document_to_s3
from app.services.textract_text_extraction_service import maybe_extract_text_and_log
from app.utils.passwords import hash_password, verify_password

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


@router.get("/ping", tags=["health"])
def ping() -> dict:
    return {"message": "pong"}


class UploadDocumentResponse(BaseModel):
    bucket: str
    key: str
    s3_uri: str
    content_type: Optional[str] = None
    size_bytes: Optional[int] = None


@router.post(
    "/documents/upload",
    tags=["documents"],
    status_code=status.HTTP_201_CREATED,
    response_model=UploadDocumentResponse,
)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    prefix: Optional[str] = Form(default=None),
    db: Session = Depends(get_db),
) -> UploadDocumentResponse:
    payload = get_token_payload(request)
    tenant_id = payload.get("tenant_id") if isinstance(payload.get("tenant_id"), int) else None

    filename = (file.filename or "").strip()
    if not filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")

    try:
        result = upload_document_to_s3(
            file_obj=file.file,
            filename=filename,
            content_type=file.content_type,
            tenant_id=tenant_id,
            prefix=prefix,
        )
    except S3NotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except S3UploadError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    # Run text extraction directly in the request.
    maybe_extract_text_and_log(
        bucket=result.bucket,
        key=result.key,
        s3_uri=result.s3_uri,
        filename=filename,
        content_type=file.content_type,
    )

    if is_pdfplumber_enabled_on_upload() and is_db_configured():
        try:
            extraction = extract_and_store_text_pdfplumber(
                db=db,
                tenant_id=tenant_id,
                bucket=result.bucket,
                key=result.key,
                s3_uri=result.s3_uri,
                filename=filename,
                content_type=file.content_type,
                size_bytes=result.size_bytes,
            )
            chunk_and_store(db=db, extraction=extraction)
        except Exception:
            logger.exception(
                "pdfplumber extraction or chunking failed",
                extra={"s3_uri": result.s3_uri},
            )

    return UploadDocumentResponse(
        bucket=result.bucket,
        key=result.key,
        s3_uri=result.s3_uri,
        content_type=result.content_type,
        size_bytes=result.size_bytes,
    )


class DocumentSummary(BaseModel):
    id: int
    filename: Optional[str]
    content_type: Optional[str]
    size_bytes: Optional[int]
    s3_uri: str
    status: str
    extraction_method: str
    extracted_char_count: int
    error_message: Optional[str]
    extraction_completed: bool
    chunking_completed: bool
    embedding_completed: bool
    tenant_id: Optional[int]
    extracted_at: Optional[datetime]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class DocumentListResponse(BaseModel):
    total: int
    skip: int
    limit: int
    documents: List[DocumentSummary]


@router.get("/documents", tags=["documents"], response_model=DocumentListResponse)
def list_documents(
    request: Request,
    skip: int = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
) -> DocumentListResponse:
    if not is_db_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database not configured",
        )

    payload = get_token_payload(request)
    is_super_admin = payload.get("is_super_admin", False)

    query = db.query(DocumentTextExtraction)

    if not is_super_admin:
        tenant_id = payload.get("tenant_id")
        if not isinstance(tenant_id, int):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tenant context required")
        query = query.filter(DocumentTextExtraction.tenant_id == tenant_id)

    total = query.count()
    documents = (
        query.order_by(DocumentTextExtraction.created_at.desc())
        .offset(skip)
        .limit(limit)
        .all()
    )

    return DocumentListResponse(
        total=total,
        skip=skip,
        limit=limit,
        documents=[DocumentSummary.model_validate(doc) for doc in documents],
    )


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


class SendEmailRequest(BaseModel):
    to_email: EmailStr
    to_name: str = Field(min_length=1, max_length=255)
    subject: str = Field(min_length=1, max_length=998)
    html_content: str = Field(min_length=1)
    text_content: Optional[str] = None


class SendEmailResponse(BaseModel):
    message_id: str


@router.post(
    "/email/send",
    tags=["email"],
    status_code=status.HTTP_200_OK,
    response_model=SendEmailResponse,
)
def send_email_endpoint(
    payload: SendEmailRequest,
    request: Request,
) -> SendEmailResponse:
    get_token_payload(request)  # requires valid JWT

    try:
        message_id = send_email(
            to_email=str(payload.to_email),
            to_name=payload.to_name,
            subject=payload.subject,
            html_content=payload.html_content,
            text_content=payload.text_content,
        )
    except BrevoNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except BrevoSendError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc

    return SendEmailResponse(message_id=message_id)


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
