import hashlib
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from google.api_core.exceptions import AlreadyExists

from firebase_config import db


router = APIRouter(
    prefix="/medicine-favorites",
    tags=["Medicine Favorites"],
)

COLLECTION = "medicineFavorites"


# ======================================
# 요청 모델
# ======================================

class FavoriteCreateRequest(BaseModel):
    userId: str = Field(min_length=1)
    medicineName: str = Field(min_length=1)
    itemSeq: Optional[str] = None


class FavoriteDeleteRequest(BaseModel):
    userId: str = Field(min_length=1)


# ======================================
# 공통 함수
# ======================================

def _get_user(user_id: str):
    user_id = user_id.strip()

    if not user_id:
        raise HTTPException(
            status_code=400,
            detail="userId가 필요합니다.",
        )

    snap = (
        db.collection("users")
        .document(user_id)
        .get()
    )

    if not snap.exists:
        raise HTTPException(
            status_code=404,
            detail="사용자를 찾을 수 없습니다.",
        )

    return user_id


def _normalize_name(name: str) -> str:
    # 공백 차이로 중복 저장되지 않도록 정규화
    return re.sub(
        r"\s+",
        " ",
        name.strip(),
    )


def _favorite_id(
    user_id: str,
    medicine_name: str,
    item_seq: Optional[str],
) -> str:

    # 품목코드가 있으면 품목코드를 우선 사용
    if item_seq:
        medicine_key = (
            "itemSeq:" + item_seq.strip()
        )
    else:
        medicine_key = (
            "name:" + medicine_name.casefold()
        )

    raw = f"{user_id}|{medicine_key}"

    return hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()


def _format_favorite(snap):
    data = snap.to_dict() or {}

    created_at = data.get("createdAt")

    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()

    return {
        "favoriteId": snap.id,
        "medicineName": data.get(
            "medicineName", ""
        ),
        "itemSeq": data.get("itemSeq"),
        "createdAt": created_at,
    }


# ======================================
# 1. 즐겨찾기 추가
# ======================================

@router.post("")
def add_favorite(req: FavoriteCreateRequest):
    user_id = _get_user(req.userId)

    medicine_name = _normalize_name(
        req.medicineName
    )

    item_seq = (
        str(req.itemSeq).strip()
        if req.itemSeq is not None
        else None
    )

    if item_seq == "":
        item_seq = None

    if not medicine_name:
        raise HTTPException(
            status_code=400,
            detail="약 이름을 입력해 주세요.",
        )

    favorite_id = _favorite_id(
        user_id,
        medicine_name,
        item_seq,
    )

    ref = (
        db.collection(COLLECTION)
        .document(favorite_id)
    )

    try:
        ref.create({
            "userId": user_id,
            "medicineName": medicine_name,
            "itemSeq": item_seq,
            "category": "stock",
            "createdAt": datetime.now(
                timezone.utc
            ),
        })

        already_exists = False

    except AlreadyExists:
        already_exists = True

    return {
        "success": True,
        "favoriteId": favorite_id,
        "medicineName": medicine_name,
        "itemSeq": item_seq,
        "alreadyExists": already_exists,
        "message": (
            "이미 즐겨찾기한 약입니다."
            if already_exists
            else "즐겨찾기에 추가했습니다."
        ),
    }


# ======================================
# 2. 즐겨찾기 목록 조회
# ======================================

@router.get("")
def get_favorites(
    userId: str = Query(...),
):
    user_id = _get_user(userId)

    docs = (
        db.collection(COLLECTION)
        .where("userId", "==", user_id)
        .stream()
    )

    favorites = [
        _format_favorite(doc)
        for doc in docs
    ]

    # 최근 즐겨찾기한 약이 먼저 표시됨
    favorites.sort(
        key=lambda item: (
            item["createdAt"] or ""
        ),
        reverse=True,
    )

    return {
        "success": True,
        "count": len(favorites),
        "favorites": favorites,
    }


# ======================================
# 3. 즐겨찾기 여부 확인
# ======================================

@router.get("/check")
def check_favorite(
    userId: str = Query(...),
    medicineName: str = Query(...),
    itemSeq: Optional[str] = Query(None),
):
    user_id = _get_user(userId)

    medicine_name = _normalize_name(
        medicineName
    )

    if not medicine_name:
        raise HTTPException(
            status_code=400,
            detail="약 이름을 입력해 주세요.",
        )

    clean_item_seq = (
        itemSeq.strip()
        if itemSeq
        else None
    )

    favorite_id = _favorite_id(
        user_id,
        medicine_name,
        clean_item_seq,
    )

    snap = (
        db.collection(COLLECTION)
        .document(favorite_id)
        .get()
    )

    return {
        "success": True,
        "isFavorite": snap.exists,
        "favoriteId": (
            favorite_id if snap.exists
            else None
        ),
    }


# ======================================
# 4. 즐겨찾기 해제
# ======================================

@router.delete("/{favorite_id}")
def delete_favorite(
    favorite_id: str,
    req: FavoriteDeleteRequest,
):
    user_id = _get_user(req.userId)

    ref = (
        db.collection(COLLECTION)
        .document(favorite_id)
    )

    snap = ref.get()

    if not snap.exists:
        raise HTTPException(
            status_code=404,
            detail="즐겨찾기를 찾을 수 없습니다.",
        )

    data = snap.to_dict() or {}

    if data.get("userId") != user_id:
        raise HTTPException(
            status_code=403,
            detail="다른 사용자의 즐겨찾기입니다.",
        )

    ref.delete()

    return {
        "success": True,
        "favoriteId": favorite_id,
        "message": "즐겨찾기를 해제했습니다.",
    }
