
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr, Field

from services.email_verification_service import (
    send_verification_code,
    verify_email_code,
)

router = APIRouter(
    prefix="/email-verification",
    tags=["Email Verification"],
)


class SendCodeRequest(BaseModel):
    email: EmailStr


class VerifyCodeRequest(BaseModel):
    email: EmailStr
    code: str = Field(
        ...,
        pattern=r"^\d{6}$",
    )


@router.post("/send")
def send_code(request: SendCodeRequest):
    try:
        return send_verification_code(request.email)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )


@router.post("/verify")
def verify_code(request: VerifyCodeRequest):
    try:
        return verify_email_code(
            request.email,
            request.code,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail=str(e),
        )
