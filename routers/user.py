from fastapi import APIRouter, HTTPException
from datetime import datetime
import uuid

from firebase_config import db
from models.schemas import (
    UserCreateRequest,
    UserLoginRequest,
)

from services.password_service import (
    hash_password,
    verify_user_password,
)
from services.guardian_service import (
    get_linked_guardian_phone,
)


router = APIRouter(
    prefix="/users",
    tags=["Users"]
)


# ============================================================
# 사용자 생성
# ============================================================

@router.post("/create")
def create_user(
    data: UserCreateRequest
):

    existing_users = list(
        db.collection("users")
        .where(
            "email",
            "==",
            data.email
        )
        .limit(1)
        .stream()
    )

    if existing_users:
        raise HTTPException(
            status_code=409,
            detail="이미 가입된 이메일입니다."
        )

    user_id = str(
        uuid.uuid4()
    )

    # ✅ 비밀번호 암호화
    password_hash = (
        hash_password(
            data.password
        )
    )

    user_data = {
        "userId":
            user_id,

        "name":
            data.name,

        "email":
            data.email,

        "phone":
            data.phone,

        "passwordHash":
            password_hash,

        "gender":
            data.gender,

        "pregnancy":
            data.pregnancy,

        "birthYear":
            data.birthYear,

        "birthMonth":
            data.birthMonth,

        "birthDay":
            data.birthDay,

        "guardianPhone":
            data.guardianPhone,

        "allergyList":
            data.allergyList,

        "createdAt":
            datetime.now().isoformat()
    }

    db.collection(
        "users"
    ).document(
        user_id
    ).set(
        user_data
    )

    response_user = (
        user_data.copy()
    )

    response_user.pop(
        "passwordHash",
        None
    )

    return {
        "success":
            True,

        "message":
            "사용자 정보 저장 완료",

        "userId":
            user_id,

        "user":
            response_user
    }


# ============================================================
# 로그인
# ============================================================

@router.post("/login")
def login_user(
    data: UserLoginRequest
):

    users = list(
        db.collection("users")
        .where(
            "email",
            "==",
            data.email
        )
        .limit(1)
        .stream()
    )

    if not users:
        raise HTTPException(
            status_code=401,
            detail=
                "이메일 또는 비밀번호가 올바르지 않습니다."
        )

    user_doc = users[0]

    user_data = (
        user_doc.to_dict()
    )

    # ✅ password_service 사용
    if not verify_user_password(
        user_data,
        data.password,
    ):
        raise HTTPException(
            status_code=401,
            detail=
                "이메일 또는 비밀번호가 올바르지 않습니다."
        )

    user_data.pop(
        "passwordHash",
        None
    )

    return {
        "success":
            True,

        "message":
            "로그인 성공",

        "userId":
            user_data["userId"],

        "user":
            user_data
    }


# ============================================================
# 사용자 정보 조회
# ============================================================

@router.get("/{user_id}")
def get_user(
    user_id: str
):

    user_ref = (
        db.collection(
            "users"
        ).document(
            user_id
        )
    )

    user_doc = (
        user_ref.get()
    )

    if not user_doc.exists:
        return {
            "success":
                False,

            "message":
                "사용자를 찾을 수 없습니다."
        }

    user_data = (
        user_doc.to_dict()
    )

    user_data.pop(
        "passwordHash",
        None
    )
    linked_guardian_phone = (
        get_linked_guardian_phone(
            user_id
        )
    )

    if linked_guardian_phone:
        user_data[
            "guardianPhone"
        ] = linked_guardian_phone
    return {
        "success":
            True,

        "user":
            user_data
    }