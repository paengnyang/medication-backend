from fastapi import APIRouter

from pydantic import (
    BaseModel,
    EmailStr,
    Field,
)

from services.password_reset_service import (
    request_password_reset,
    verify_reset_code,
    confirm_password_reset,
)


router = APIRouter(
    prefix="/auth/password-reset",
    tags=["Password Reset"],
)


class PasswordResetRequest(
    BaseModel
):
    email: EmailStr


class PasswordResetVerifyRequest(
    BaseModel
):
    email: EmailStr

    code: str = Field(
        ...,
        min_length=6,
        max_length=6,
    )


class PasswordResetConfirmRequest(
    BaseModel
):
    resetToken: str = Field(
        ...,
        min_length=20,
    )

    newPassword: str = Field(
        ...,
        min_length=8,
    )


@router.post("/request")
def request_reset(
    request:
        PasswordResetRequest
):
    result = request_password_reset(
        request.email
    )

    response = {
        "success":
            True,

        "message":
            "가입된 이메일인 경우 비밀번호 재설정 인증번호가 생성되었습니다."
    }

    if result.get(
        "debugCode"
    ):
        response[
            "debugCode"
        ] = result[
            "debugCode"
        ]

    return response


@router.post("/verify")
def verify_code(
    request:
        PasswordResetVerifyRequest
):
    try:
        result = (
            verify_reset_code(
                email=
                    request.email,

                code=
                    request.code,
            )
        )

        return {
            "success":
                True,

            "message":
                "인증번호 확인 완료",

            **result,
        }

    except ValueError as e:
        return {
            "success":
                False,

            "message":
                str(e),
        }


@router.post("/confirm")
def confirm_reset(
    request:
        PasswordResetConfirmRequest
):
    try:
        result = (
            confirm_password_reset(
                reset_token=
                    request.resetToken,

                new_password=
                    request.newPassword,
            )
        )

        return {
            "success":
                True,

            "message":
                "비밀번호가 변경되었습니다.",

            **result,
        }

    except ValueError as e:
        return {
            "success":
                False,

            "message":
                str(e),
        }