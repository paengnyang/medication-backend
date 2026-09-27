import hashlib
import secrets

from datetime import (
    datetime,
    timedelta,
    timezone,
)

from firebase_config import db


GUARDIAN_CODE_EXPIRE_MINUTES = 10


# ============================================================
# 현재 시간
# ============================================================

def _now():
    return datetime.now(
        timezone.utc
    )


# ============================================================
# 연결 코드 해시
# ============================================================

def _hash_code(
    code: str
) -> str:

    return hashlib.sha256(
        code.encode("utf-8")
    ).hexdigest()


# ============================================================
# 사용자 조회
# ============================================================

def _get_user(
    user_id: str
):

    user_id = str(
        user_id
    ).strip()

    if not user_id:
        return None

    doc = (
        db.collection("users")
        .document(user_id)
        .get()
    )

    if not doc.exists:
        return None

    data = doc.to_dict() or {}

    return {
        "userId": doc.id,
        "name": data.get("name"),
        "email": data.get("email"),
        "phone": data.get("phone"),
    }


# ============================================================
# 보호자 연결 코드 생성
# ============================================================

def create_guardian_code(
    ward_user_id: str
):

    ward_user_id = str(
        ward_user_id
    ).strip()

    ward = _get_user(
        ward_user_id
    )

    if ward is None:
        raise ValueError(
            "사용자 정보를 찾을 수 없습니다."
        )

    # 100000 ~ 999999
    code = str(
        secrets.randbelow(
            900000
        )
        + 100000
    )

    now = _now()

    expires_at = (
        now
        + timedelta(
            minutes=
                GUARDIAN_CODE_EXPIRE_MINUTES
        )
    )

    doc_ref = (
        db.collection(
            "guardian_link_codes"
        )
        .document()
    )

    doc_ref.set({
        "wardUserId":
            ward_user_id,

        # 실제 코드는 Firestore에 평문 저장하지 않음
        "codeHash":
            _hash_code(code),

        "createdAt":
            now,

        "expiresAt":
            expires_at,

        "used":
            False,
    })

    return {
        "wardUserId":
            ward_user_id,

        "wardName":
            ward.get("name"),

        "connectionCode":
            code,

        "expiresInMinutes":
            GUARDIAN_CODE_EXPIRE_MINUTES,
    }


# ============================================================
# 연결 코드 조회
# ============================================================

def _find_connection_code(
    code: str
):

    code = str(
        code
    ).strip()

    code_hash = _hash_code(
        code
    )

    docs = (
        db.collection(
            "guardian_link_codes"
        )
        .where(
            "codeHash",
            "==",
            code_hash,
        )
        .stream()
    )

    for doc in docs:

        data = (
            doc.to_dict()
            or {}
        )

        # 이미 사용한 코드
        if data.get(
            "used",
            False,
        ):
            continue

        expires_at = (
            data.get(
                "expiresAt"
            )
        )

        # 만료된 코드
        if (
            expires_at is None
            or expires_at < _now()
        ):
            continue

        return {
            "documentId":
                doc.id,

            "data":
                data,
        }

    return None


# ============================================================
# 기존 연결 확인
# ============================================================

def _find_existing_link(
    ward_user_id: str,
    guardian_user_id: str,
):

    docs = (
        db.collection(
            "guardian_links"
        )
        .where(
            "wardUserId",
            "==",
            ward_user_id,
        )
        .stream()
    )

    for doc in docs:

        data = (
            doc.to_dict()
            or {}
        )

        if (
            data.get(
                "guardianUserId"
            )
            == guardian_user_id
            and
            data.get(
                "status"
            )
            == "active"
        ):
            return {
                "linkId":
                    doc.id,

                **data,
            }

    return None


# ============================================================
# 보호자 연결
# ============================================================

def connect_guardian(
    guardian_user_id: str,
    connection_code: str,
):

    guardian_user_id = str(
        guardian_user_id
    ).strip()

    connection_code = str(
        connection_code
    ).strip()

    guardian = _get_user(
        guardian_user_id
    )

    if guardian is None:
        raise ValueError(
            "보호자 계정을 찾을 수 없습니다."
        )

    code_result = (
        _find_connection_code(
            connection_code
        )
    )

    if code_result is None:
        raise ValueError(
            "연결 코드가 올바르지 않거나 만료되었습니다."
        )

    code_data = (
        code_result["data"]
    )

    ward_user_id = (
        code_data.get(
            "wardUserId"
        )
    )

    if not ward_user_id:
        raise ValueError(
            "연결 대상 사용자 정보가 없습니다."
        )

    # 자기 자신 연결 방지
    if (
        guardian_user_id
        == ward_user_id
    ):
        raise ValueError(
            "자기 자신을 보호자로 연결할 수 없습니다."
        )

    ward = _get_user(
        ward_user_id
    )

    if ward is None:
        raise ValueError(
            "연결 대상 사용자를 찾을 수 없습니다."
        )

    existing_link = (
        _find_existing_link(
            ward_user_id,
            guardian_user_id,
        )
    )

    if existing_link:
        raise ValueError(
            "이미 연결된 보호자입니다."
        )

    # ========================================
    # 실제 연결 정보 저장
    # ========================================

    link_ref = (
        db.collection(
            "guardian_links"
        )
        .document()
    )

    link_data = {
        "wardUserId":
            ward_user_id,

        "guardianUserId":
            guardian_user_id,

        "status":
            "active",

        "createdAt":
            _now(),
    }

    link_ref.set(
        link_data
    )

    # 연결 코드 사용 완료 처리
    code_ref = (
        db.collection(
            "guardian_link_codes"
        )
        .document(
            code_result[
                "documentId"
            ]
        )
    )

    code_ref.update({
        "used":
            True,

        "usedAt":
            _now(),

        "guardianUserId":
            guardian_user_id,
    })

    return {
        "linkId":
            link_ref.id,

        "ward": {
            "userId":
                ward_user_id,

            "name":
                ward.get("name"),
        },

        "guardian": {
            "userId":
                guardian_user_id,

            "name":
                guardian.get("name"),
        },

        "status":
            "active",
    }


# ============================================================
# 복약 사용자의 보호자 목록
# ============================================================

def get_guardians_for_ward(
    ward_user_id: str
):

    docs = (
        db.collection(
            "guardian_links"
        )
        .where(
            "wardUserId",
            "==",
            ward_user_id,
        )
        .stream()
    )

    result = []

    for doc in docs:

        data = (
            doc.to_dict()
            or {}
        )

        if data.get(
            "status"
        ) != "active":
            continue

        guardian_user_id = (
            data.get(
                "guardianUserId"
            )
        )

        guardian = _get_user(
            guardian_user_id
        )

        result.append({
            "linkId":
                doc.id,

            "guardianUserId":
                guardian_user_id,

            "guardianName":
                (
                    guardian.get("name")
                    if guardian
                    else None
                ),

            "status":
                "active",
        })

    return result


# ============================================================
# 보호자가 관리하는 사용자 목록
# ============================================================

def get_wards_for_guardian(
    guardian_user_id: str
):

    docs = (
        db.collection(
            "guardian_links"
        )
        .where(
            "guardianUserId",
            "==",
            guardian_user_id,
        )
        .stream()
    )

    result = []

    for doc in docs:

        data = (
            doc.to_dict()
            or {}
        )

        if data.get(
            "status"
        ) != "active":
            continue

        ward_user_id = (
            data.get(
                "wardUserId"
            )
        )

        ward = _get_user(
            ward_user_id
        )

        result.append({
            "linkId":
                doc.id,

            "wardUserId":
                ward_user_id,

            "wardName":
                (
                    ward.get("name")
                    if ward
                    else None
                ),

            "status":
                "active",
        })

    return result