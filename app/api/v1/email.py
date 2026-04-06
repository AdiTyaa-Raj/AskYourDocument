from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field

from app.middleware.auth import get_token_payload
from app.services.email_service import BrevoNotConfiguredError, BrevoSendError, send_email

router = APIRouter()
logger = logging.getLogger(__name__)


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
