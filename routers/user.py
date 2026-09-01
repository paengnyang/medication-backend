from fastapi import APIRouter, HTTPException
from datetime import datetime
import uuid
import bcrypt

from firebase_config import db
from models.schemas import UserCreateRequest, UserLoginRequest


router = APIRouter(
    prefix="/users",
    tags=["Users"]
)


# 사용자 생성 API
@router.post("/create")
def create_user(data: UserCreateRequest):

    # 같은 이메일이 이미 존재하는지 검사
    existing_users = list(
        db.collection("users")
        .where("email", "==", data.email)
        .limit(1)
        .stream()
    )

    if existing_users:
        raise HTTPException(
            status_code=409,
            detail="이미 가입된 이메일입니다."
        )

    # 사용자 ID 생성
    user_id = str(uuid.uuid4())

    # 비밀번호 암호화
    password_hash = bcrypt.hashpw(
        data.password.encode("utf-8"),
        bcrypt.gensalt()
    ).decode("utf-8")

    user_data = {
        "userId": user_id,
        "name": data.name,
        "email": data.email,
        "phone": data.phone,

        # ✅ 평문 비밀번호가 아니라 암호화된 값 저장
        "passwordHash": password_hash,

        "gender": data.gender,
        "pregnancy": data.pregnancy,

        "birthYear": data.birthYear,
        "birthMonth": data.birthMonth,
        "birthDay": data.birthDay,

        "guardianPhone": data.guardianPhone,

        "allergyList": data.allergyList,

        "createdAt": datetime.now().isoformat()
    }

    db.collection("users").document(user_id).set(user_data)

    # Flutter에 passwordHash는 보내지 않음
    response_user = user_data.copy()
    response_user.pop("passwordHash", None)

    return {
        "success": True,
        "message": "사용자 정보 저장 완료",
        "userId": user_id,
        "user": response_user
    }


# ✅ 로그인 API
@router.post("/login")
def login_user(data: UserLoginRequest):

    # 이메일로 사용자 검색
    users = list(
        db.collection("users")
        .where("email", "==", data.email)
        .limit(1)
        .stream()
    )

    # 이메일이 존재하지 않음
    if not users:
        raise HTTPException(
            status_code=401,
            detail="이메일 또는 비밀번호가 올바르지 않습니다."
        )

    user_doc = users[0]
    user_data = user_doc.to_dict()

    stored_hash = user_data.get("passwordHash")

    if not stored_hash:
        raise HTTPException(
            status_code=401,
            detail="이메일 또는 비밀번호가 올바르지 않습니다."
        )

    # ✅ 입력한 비밀번호와 저장된 암호 비교
    password_correct = bcrypt.checkpw(
        data.password.encode("utf-8"),
        stored_hash.encode("utf-8")
    )

    if not password_correct:
        raise HTTPException(
            status_code=401,
            detail="이메일 또는 비밀번호가 올바르지 않습니다."
        )

    # passwordHash는 Flutter로 보내지 않음
    user_data.pop("passwordHash", None)

    return {
        "success": True,
        "message": "로그인 성공",
        "userId": user_data["userId"],
        "user": user_data
    }


# 사용자 정보 조회 API
@router.get("/{user_id}")
def get_user(user_id: str):

    user_ref = db.collection("users").document(user_id)
    user_doc = user_ref.get()

    if not user_doc.exists:
        return {
            "success": False,
            "message": "사용자를 찾을 수 없습니다."
        }

    user_data = user_doc.to_dict()

    # 비밀번호 해시는 반환하지 않음
    user_data.pop("passwordHash", None)

    return {
        "success": True,
        "user": user_data
    }