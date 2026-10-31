
import os
import re
import hmac
import hashlib
import secrets
import smtplib
import mimetypes

from pathlib import Path
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import make_msgid

from dotenv import load_dotenv
from firebase_config import db


# ============================================================
# 환경변수
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")

# 백엔드에 저장할 MediCare 로고 이미지
LOGO_PATH = BASE_DIR / "assets" / "medicare_logo.png"

CODE_MINUTES = 5
RESEND_SECONDS = 60
MAX_ATTEMPTS = 5
TICKET_MINUTES = 10


# ============================================================
# 공통 함수
# ============================================================

def _now():
    return datetime.now(timezone.utc)


def _normalize_email(email):
    email = str(email).strip().lower()

    if not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+",
        email,
    ):
        raise ValueError(
            "올바른 이메일 주소를 입력해주세요."
        )

    return email


def _secret():
    value = os.getenv(
        "EMAIL_VERIFICATION_SECRET", ""
    )

    if len(value) < 32:
        raise RuntimeError(
            "EMAIL_VERIFICATION_SECRET 설정이 필요합니다."
        )

    return value.encode("utf-8")


def _code_hash(email, code):
    return hmac.new(
        _secret(),
        f"{email}:{code}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _token_hash(token):
    return hashlib.sha256(
        token.encode("utf-8")
    ).hexdigest()


def _doc_ref(email):
    email = _normalize_email(email)

    doc_id = hashlib.sha256(
        email.encode("utf-8")
    ).hexdigest()

    return (
        db.collection("email_verifications")
        .document(doc_id)
    )


# ============================================================
# HTML 이메일 템플릿
# ============================================================

def build_verification_email_html(
    code: str,
    expire_minutes: int = 5,
    logo_cid: str | None = None,
    purpose: str = "signup",
):
    is_reset = purpose == "password_reset"

    mail_title = (
        "비밀번호 재설정 안내"
        if is_reset else "이메일 인증 안내"
    )

    mail_category = (
        "비밀번호 재설정"
        if is_reset else "이메일 계정 인증"
    )

    mail_description = (
        "비밀번호 재설정을 위한 인증번호입니다.<br>"
        "아래 인증번호를 입력해 주세요."
        if is_reset
        else
        "이메일 계정 인증을 위한 인증번호입니다.<br>"
        "아래 인증번호를 입력하여 인증을 완료해 주세요."
    )

    mail_notice = (
        "MediCare 비밀번호 재설정을 위한"
        if is_reset
        else "MediCare 이메일 인증을 위한"
    )
    if logo_cid:
        logo_html = f"""
        <img
            src="cid:{logo_cid}"
            alt="MediCare"
            width="180"
            style="
                display:block;
                width:180px;
                max-width:100%;
                height:auto;
                border:0;
            "
        />
        """
    else:
        logo_html = """
        <div style="
            font-size:30px;
            font-weight:800;
            letter-spacing:-1px;
            color:#ffffff;
        ">
            MediCare
        </div>
        """

    return f"""
<!DOCTYPE html>
<html lang="ko">
<head>
    <meta charset="UTF-8">
    <meta name="viewport"
          content="width=device-width, initial-scale=1.0">
    <title>MediCare 이메일 인증</title>
</head>

<body style="
    margin:0;
    padding:0;
    background:#f5f6f8;
    font-family:Arial, 'Malgun Gothic', sans-serif;
    color:#171717;
">

<table role="presentation"
       width="100%"
       cellspacing="0"
       cellpadding="0"
       border="0"
       style="background:#f5f6f8;">

<tr>
<td align="center" style="padding:30px 14px;">

<table role="presentation"
       width="100%"
       cellspacing="0"
       cellpadding="0"
       border="0"
       style="
           max-width:640px;
           background:#ffffff;
           border-radius:14px;
           overflow:hidden;
       ">

    <!-- 로고 -->
    <tr>
        <td align="center"
            style="
                background:#dcd4f0;
                padding:34px 20px;
            ">
            {logo_html}
        </td>
    </tr>

    <!-- 본문 -->
    <tr>
        <td style="padding:42px 36px 40px;">

            <div style="
                font-size:14px;
                color:#777777;
                margin-bottom:12px;
            ">
                이메일 계정 인증
            </div>

            <h1 style="
                font-size:32px;
                font-weight:700;
                line-height:1.3;
                margin:0 0 30px;
                color:#171717;
            ">
                이메일 인증 안내
            </h1>

            <div style="
                border-top:1px solid #ededed;
                margin-bottom:30px;
            "></div>

            <p style="
                font-size:17px;
                line-height:1.8;
                margin:0 0 8px;
            ">
                MediCare 서비스를 이용해 주셔서 감사합니다.
            </p>

            <p style="
                font-size:17px;
                line-height:1.8;
                margin:0 0 30px;
            ">
                이메일 계정 인증을 위한 인증번호입니다.<br>
                아래 인증번호를 입력하여 인증을 완료해 주세요.
            </p>

            <!-- 인증번호 영역 -->
            <table role="presentation"
                   width="100%"
                   cellspacing="0"
                   cellpadding="0"
                   border="0"
                   style="
                       background:#f4f5f7;
                       border-radius:12px;
                   ">

                <tr>
                    <td align="center"
                        style="padding:35px 12px;">

                        <div style="
                            font-size:42px;
                            font-weight:700;
                            letter-spacing:7px;
                            color:#111111;
                            line-height:1.4;
                            text-align:center;
                            white-space:nowrap;
                        ">
                            {code}
                        </div>

                        <p style="
                            font-size:14px;
                            color:#909090;
                            line-height:1.8;
                            margin:22px 0 0;
                        ">
                            이 인증번호는 발송 시점부터
                            {expire_minutes}분간 유효합니다.<br>
                            본인이 요청하지 않았다면
                            이 메일을 무시해 주세요.
                        </p>

                    </td>
                </tr>

            </table>

            <div style="height:36px;"></div>

            <!-- 보안 안내 -->
            <table role="presentation"
                   width="100%"
                   cellspacing="0"
                   cellpadding="0"
                   border="0"
                   style="
                       background:#f8f9fa;
                       border-radius:12px;
                   ">

                <tr>
                    <td align="center"
                        style="
                            padding:25px 20px;
                            color:#999999;
                            font-size:13px;
                            line-height:1.9;
                        ">

                        본 메일은 MediCare 이메일 인증을 위한
                        자동 발송 메일입니다.<br>

                        회신하셔도 답변이 제공되지 않습니다.<br>

                        인증번호는 다른 사람에게
                        절대 공유하지 마세요.

                    </td>
                </tr>

            </table>

        </td>
    </tr>

    <!-- 푸터 -->
    <tr>
        <td align="center"
            style="
                padding:0 24px 35px;
                font-size:13px;
                color:#999999;
                line-height:1.8;
            ">

            <strong style="color:#555555;">
                MediCare
            </strong><br>

            더 안전하고 편리한 복약 관리 서비스<br>

            © MediCare. All rights reserved.

        </td>
    </tr>

</table>

</td>
</tr>
</table>

</body>
</html>
"""


# ============================================================
# 실제 인증 이메일 발송
# ============================================================

def send_verification_email(
    to_email: str,
    code: str,
    expire_minutes: int = CODE_MINUTES,
    purpose: str = "signup",
):
    if not SMTP_USER or not SMTP_PASSWORD:
        raise RuntimeError(
            "SMTP 이메일 발송 설정이 없습니다."
        )

    message = EmailMessage()

    message["Subject"] = (
        "[MediCare] 비밀번호 재설정 인증번호"
        if purpose == "password_reset"
        else "[MediCare] 이메일 인증 안내"
    )

    message["From"] = SMTP_USER
    message["To"] = to_email

    email_type = (
        "비밀번호 재설정"
        if purpose == "password_reset"
        else "이메일 인증"
    )

    message.set_content(
        f"{email_type} 인증번호: {code}\n"
        f"유효기간: {expire_minutes}분\n\n"
        "본인이 요청하지 않았다면 "
        "이 메일을 무시해주세요."
    )

    logo_cid = None
    logo_bytes = None
    logo_subtype = None

    # 로고 파일이 있을 경우 메일에 이미지 포함
    if LOGO_PATH.is_file():
        content_type, _ = mimetypes.guess_type(
            str(LOGO_PATH)
        )

        if content_type in (
            "image/png",
            "image/jpeg",
            "image/gif",
        ):
            logo_bytes = LOGO_PATH.read_bytes()
            logo_subtype = content_type.split("/")[1]

            # Content-ID 생성
            logo_cid = make_msgid(
                domain="medicare.local"
            )[1:-1]

    html_content = build_verification_email_html(
        code=code,
        expire_minutes=expire_minutes,
        logo_cid=logo_cid,
        purpose=purpose,
    )

    message.add_alternative(
        html_content,
        subtype="html",
    )

    # HTML 본문에 이미지 삽입
    if logo_bytes is not None and logo_cid:
        html_part = message.get_payload()[-1]

        html_part.add_related(
            logo_bytes,
            maintype="image",
            subtype=logo_subtype,
            cid=f"<{logo_cid}>",
            filename=LOGO_PATH.name,
            disposition="inline",
        )

    # SMTP 전송
    with smtplib.SMTP_SSL(
        SMTP_HOST,
        SMTP_PORT,
        timeout=15,
    ) as smtp:
        smtp.login(
            SMTP_USER,
            SMTP_PASSWORD,
        )

        smtp.send_message(message)


# ============================================================
# 인증번호 발송 요청
# ============================================================

def send_verification_code(email):
    email = _normalize_email(email)

    # 비밀키 설정 확인
    _secret()

    ref = _doc_ref(email)
    now = _now()

    existing = ref.get()

    if existing.exists:
        data = existing.to_dict() or {}
        last_sent = data.get("sentAt")

        if (
            isinstance(last_sent, datetime)
            and now - last_sent
            < timedelta(seconds=RESEND_SECONDS)
        ):
            raise ValueError(
                "인증번호는 60초 후 다시 발송할 수 있습니다."
            )

    # 6자리 숫자 인증번호
    code = f"{secrets.randbelow(1000000):06d}"

    code_hash = _code_hash(
        email,
        code,
    )

    # 메일 발송
    send_verification_email(
        to_email=email,
        code=code,
        expire_minutes=CODE_MINUTES,
    )

    # 인증번호 원문은 저장하지 않음
    ref.set({
        "email": email,
        "codeHash": code_hash,
        "createdAt": now,
        "sentAt": now,
        "expiresAt": now + timedelta(
            minutes=CODE_MINUTES
        ),
        "attempts": 0,
        "verified": False,
        "used": False,
        "verificationTokenHash": None,
        "verifiedUntil": None,
    })

    return {
        "success": True,
        "message": (
            "인증번호를 이메일로 전송했습니다."
        ),
    }


# ============================================================
# 인증번호 확인
# ============================================================

def verify_email_code(email, code):
    email = _normalize_email(email)
    code = str(code).strip()

    ref = _doc_ref(email)

    doc = ref.get()

    if not doc.exists:
        raise ValueError(
            "인증번호를 먼저 발송해주세요."
        )

    data = doc.to_dict() or {}

    if data.get("used") is True:
        raise ValueError(
            "이미 사용된 인증 정보입니다."
        )

    if (
        data.get("verified") is True
        or not data.get("codeHash")
    ):
        raise ValueError(
            "이미 인증된 번호입니다."
        )

    attempts = int(
        data.get("attempts", 0)
    )

    if attempts >= MAX_ATTEMPTS:
        raise ValueError(
            "인증 시도 횟수를 초과했습니다. "
            "인증번호를 다시 발송해주세요."
        )

    expires_at = data.get(
        "expiresAt"
    )

    if (
        expires_at is None
        or _now() >= expires_at
    ):
        raise ValueError(
            "인증번호가 만료되었습니다."
        )

    if not re.fullmatch(r"\d{6}", code):
        raise ValueError(
            "6자리 인증번호를 입력해주세요."
        )

    expected = data.get(
        "codeHash", ""
    )

    supplied = _code_hash(
        email,
        code,
    )

    if not hmac.compare_digest(
        expected,
        supplied,
    ):
        ref.update({
            "attempts": attempts + 1
        })

        raise ValueError(
            "인증번호가 일치하지 않습니다."
        )

    # 인증 성공 시 일회용 토큰 발급
    token = secrets.token_urlsafe(32)

    ref.update({
        "verified": True,
        "verifiedAt": _now(),
        "verificationTokenHash": _token_hash(
            token
        ),
        "verifiedUntil": _now() + timedelta(
            minutes=TICKET_MINUTES
        ),
        "codeHash": None,
    })

    return {
        "success": True,
        "message": (
            "이메일 인증이 완료되었습니다."
        ),
        "verificationToken": token,
    }


# ============================================================
# 회원가입 연동용 함수
# ============================================================

def get_email_verification_ref(
    email: str,
):
    """
    user.py의 회원가입 트랜잭션에서 사용
    """
    return _doc_ref(email)


def get_verification_token_hash(
    token: str,
) -> str:
    """
    user.py의 회원가입 트랜잭션에서 사용
    """
    return _token_hash(token)


# ============================================================
# 이전 버전 호환용
# ============================================================

def consume_email_verification(
    email,
    token,
):
    email = _normalize_email(email)

    if not token:
        raise ValueError(
            "이메일 인증이 필요합니다."
        )

    ref = _doc_ref(email)
    doc = ref.get()

    data = (
        doc.to_dict()
        if doc.exists
        else {}
    )

    if not data:
        raise ValueError(
            "이메일 인증 정보가 없습니다."
        )

    stored_hash = (
        data.get("verificationTokenHash")
        or ""
    )

    verified_until = data.get(
        "verifiedUntil"
    )

    valid = (
        data.get("email") == email
        and data.get("verified") is True
        and data.get("used") is not True
        and verified_until is not None
        and _now() < verified_until
        and hmac.compare_digest(
            stored_hash,
            _token_hash(token),
        )
    )

    if not valid:
        raise ValueError(
            "이메일 인증이 만료되었거나 "
            "유효하지 않습니다."
        )

    ref.update({
        "used": True,
        "verificationTokenHash": None,
        "usedAt": _now(),
    })
