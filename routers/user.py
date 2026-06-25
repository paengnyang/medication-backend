from fastapi import APIRouter
from datetime import datetime

from firebase_config import db
from models.schemas import UserCreateRequest


# user 관련 API를 모아두는 라우터
router = APIRouter(
    prefix="/users",
    tags=["Users"]
)


# 사용자 생성 API
@router.post("/create")
def create_user(data: UserCreateRequest):
    user_ref = db.collection("users").document(data.userId)

    user_data = {
        "userId": data.userId,
        "name": data.name,
        "email": data.email,
        "age": data.age,
        "allergyList": data.allergyList,
        "createdAt": datetime.now().isoformat()
    }

    user_ref.set(user_data)

    return {
        "success": True,
        "message": "사용자 정보 저장 완료",
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

    return {
        "success": True,
        "user": user_data
    }