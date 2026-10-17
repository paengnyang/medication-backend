import os
import secrets
import hashlib
import smtplib
from email.message import EmailMessage
from dotenv import load_dotenv

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from firebase_admin import firestore

from firebase_config import db

from services.password_service import (
    hash_password,
    verify_user_password,
)


RESET_CODE_EXPIRE_MINUTES = 10
RESET_TOKEN_EXPIRE_MINUTES = 10
MAX_VERIFY_ATTEMPTS = 5

load_dotenv()

def _now():
    return datetime.now(
        timezone.utc
    )


def _hash_secret(
    value: str
):
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def _find_user_by_email(
    email: str
):
    email = str(email).strip().lower()

    docs = (
        db.collection("users")
        .where(
            "email",
            "==",
            email,
        )
        .limit(1)
        .stream()
    )

    for doc in docs:
        return {
            "userId": doc.id,
            "data": doc.to_dict() or {},
        }

    return None


def _send_password_reset_email(
    email: str,
    code: str,
):
    smtp_user = os.getenv("SMTP_USER")
    smtp_password = os.getenv("SMTP_PASSWORD")

    if not smtp_user or not smtp_password:
        raise RuntimeError(
            "SMTP 이메일 설정이 없습니다."
        )

    message = EmailMessage()
    message["From"] = smtp_user
    message["To"] = email
    message["Subject"] = "[MediCare] 비밀번호 재설정 인증번호"

    message.set_content(
        f"비밀번호 재설정 인증번호: {code}\n"
        "인증번호는 10분간 유효합니다.\n\n"
        "본인이 요청하지 않았다면 무시해주세요."
    )

    with smtplib.SMTP_SSL(
        os.getenv("SMTP_HOST", "smtp.gmail.com"),
        int(os.getenv("SMTP_PORT", "465")),
        timeout=10,
    ) as smtp:
        smtp.login(
            smtp_user,
            smtp_password,
        )
        smtp.send_message(message)


def request_password_reset(
    email: str
):
    email = str(email).strip().lower()

    user = _find_user_by_email(
        email
    )

    if user is None:
        return {
            "requested": True,
            "debugCode": None,
        }

    code = str(
        secrets.randbelow(900000)
        + 100000
    )

    now = _now()

    expires_at = (
        now
        + timedelta(
            minutes=RESET_CODE_EXPIRE_MINUTES
        )
    )

    doc_ref = (
        db.collection(
            "password_reset_codes"
        ).document()
    )

    # 실제 Gmail로 인증번호 전송
    _send_password_reset_email(
        email=email,
        code=code,
    )
    doc_ref.set({
        "userId":
            user["userId"],

        "email":
            email,

        "codeHash":
            _hash_secret(code),

        "createdAt":
            now,

        "expiresAt":
            expires_at,

        "attempts":
            0,

        "codeVerified":
            False,

        "tokenUsed":
            False,
    })

    debug_enabled = (
        os.getenv(
            "PASSWORD_RESET_DEBUG",
            "false"
        ).lower()
        == "true"
    )

    return {
        "requested":
            True,

        "debugCode":
            code
            if debug_enabled
            else None,
    }


def _find_latest_reset_request(
    email: str
):
    docs = (
        db.collection(
            "password_reset_codes"
        )
        .where(
            "email",
            "==",
            email,
        )
        .stream()
    )

    candidates = []

    for doc in docs:
        data = doc.to_dict() or {}
        data["_documentId"] = doc.id
        candidates.append(data)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x:
            x.get("createdAt")
            or datetime.min.replace(
                tzinfo=timezone.utc
            ),
        reverse=True,
    )

    return candidates[0]


def verify_reset_code(
    email: str,
    code: str,
):
    email = str(email).strip().lower()
    code = str(code).strip()

    reset_data = (
        _find_latest_reset_request(
            email
        )
    )

    if reset_data is None:
        raise ValueError(
            "비밀번호 재설정 요청을 찾을 수 없습니다."
        )

    doc_id = (
        reset_data["_documentId"]
    )

    doc_ref = (
        db.collection(
            "password_reset_codes"
        )
        .document(doc_id)
    )

    expires_at = (
        reset_data.get(
            "expiresAt"
        )
    )

    if (
        expires_at is None
        or expires_at < _now()
    ):
        raise ValueError(
            "인증번호가 만료되었습니다."
        )

    if reset_data.get(
        "codeVerified",
        False,
    ):
        raise ValueError(
            "이미 사용된 인증번호입니다."
        )

    attempts = int(
        reset_data.get(
            "attempts",
            0
        )
    )

    if attempts >= MAX_VERIFY_ATTEMPTS:
        raise ValueError(
            "인증번호 입력 횟수를 초과했습니다."
        )

    if (
        _hash_secret(code)
        != reset_data.get(
            "codeHash"
        )
    ):
        doc_ref.update({
            "attempts":
                attempts + 1,
        })

        raise ValueError(
            "인증번호가 올바르지 않습니다."
        )

    reset_token = (
        secrets.token_urlsafe(
            32
        )
    )

    token_expires_at = (
        _now()
        + timedelta(
            minutes=RESET_TOKEN_EXPIRE_MINUTES
        )
    )

    doc_ref.update({
        "codeVerified":
            True,

        "verifiedAt":
            _now(),

        "resetTokenHash":
            _hash_secret(
                reset_token
            ),

        "tokenExpiresAt":
            token_expires_at,

        "tokenUsed":
            False,
    })

    return {
        "resetToken":
            reset_token,

        "expiresInMinutes":
            RESET_TOKEN_EXPIRE_MINUTES,
    }


def _find_by_reset_token(
    reset_token: str
):
    token_hash = (
        _hash_secret(
            reset_token
        )
    )

    docs = (
        db.collection(
            "password_reset_codes"
        )
        .where(
            "resetTokenHash",
            "==",
            token_hash,
        )
        .limit(1)
        .stream()
    )

    for doc in docs:
        return {
            "documentId":
                doc.id,

            "data":
                doc.to_dict()
                or {},
        }

    return None


def confirm_password_reset(
    reset_token: str,
    new_password: str,
):
    if len(new_password) < 8:
        raise ValueError(
            "비밀번호는 8자 이상이어야 합니다."
        )

    reset_request = (
        _find_by_reset_token(
            reset_token
        )
    )

    if reset_request is None:
        raise ValueError(
            "유효하지 않은 재설정 토큰입니다."
        )

    reset_data = (
        reset_request["data"]
    )

    if reset_data.get(
        "tokenUsed",
        False,
    ):
        raise ValueError(
            "이미 사용된 재설정 토큰입니다."
        )

    token_expires_at = (
        reset_data.get(
            "tokenExpiresAt"
        )
    )

    if (
        token_expires_at is None
        or token_expires_at < _now()
    ):
        raise ValueError(
            "재설정 토큰이 만료되었습니다."
        )

    user_id = (
        reset_data.get(
            "userId"
        )
    )

    if not user_id:
        raise ValueError(
            "사용자 정보를 찾을 수 없습니다."
        )

    password_hash = (
        hash_password(
            new_password
        )
    )

    user_ref = (
        db.collection(
            "users"
        )
        .document(
            user_id
        )
    )

    user_doc = (
        user_ref.get()
    )

    if not user_doc.exists:
        raise ValueError(
            "사용자 정보를 찾을 수 없습니다."
        )

        # 기존 비밀번호와 동일한지 검사
    user_data = user_doc.to_dict() or {}

    if verify_user_password(
        user_data,
        new_password,
    ):
        raise ValueError(
            "기존 비밀번호와 동일합니다. "
            "다른 비밀번호를 입력해주세요."
        )

    user_ref.update({
        "passwordHash":
            password_hash,

        "password":
            firestore.DELETE_FIELD,

        "passwordUpdatedAt":
            _now(),
    })

    reset_ref = (
        db.collection(
            "password_reset_codes"
        )
        .document(
            reset_request[
                "documentId"
            ]
        )
    )

    reset_ref.update({
        "tokenUsed":
            True,

        "completedAt":
            _now(),
    })

    return {
        "userId":
            user_id,

        "passwordChanged":
            True,
    }