
from datetime import date, datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from firebase_config import db


router = APIRouter(
    prefix="/medicine-recent",
    tags=["Recent Medicines"],
)


def _text(value):
    return str(value or "").strip()


def _date_string(value):
    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, date):
        return value.isoformat()

    if value is None:
        return ""

    return str(value)


def _medicine_key(name, item_seq):
    item_seq = _text(item_seq)

    if item_seq:
        return f"item:{item_seq}"

    return f"name:{''.join(name.casefold().split())}"


def _verify_user(user_id):
    user_id = _text(user_id)

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


def _read_collection(collection, user_id):
    return (
        db.collection(collection)
        .where("userId", "==", user_id)
        .stream()
    )


@router.get("")
def get_recent_medicines(
    userId: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    category: Optional[str] = Query(None),
):
    user_id = _verify_user(userId)

    if category not in (None, "stock", "prescription"):
        raise HTTPException(
            status_code=400,
            detail="category는 stock 또는 prescription이어야 합니다.",
        )

    medicines = {}

    # ---------------------------------
    # 1. 기존 복약 일정 조회
    # ---------------------------------

    for snap in _read_collection("schedules", user_id):
        data = snap.to_dict() or {}

        medicine_name = _text(
            data.get("medicineName")
        )

        if not medicine_name:
            continue

        is_stock = data.get("isStock") is True

        if category == "stock" and not is_stock:
            continue

        if category == "prescription" and is_stock:
            continue

        item_seq = (
            _text(data.get("itemSeq")) or None
        )

        key = _medicine_key(
            medicine_name,
            item_seq,
        )

        last_used = _date_string(
            data.get("updatedAt")
            or data.get("createdAt")
            or data.get("startDate")
        )

        record = {
            "medicineName": medicine_name,
            "itemSeq": item_seq,
            "category": (
                "stock" if is_stock
                else "prescription"
            ),
            "lastUsedAt": last_used,
            "registrationCount": 1,
            "scheduleId": snap.id,
            "source": "schedule",
            "requiresDosageConfirmation": True,
        }

        if key not in medicines:
            medicines[key] = record

        else:
            current = medicines[key]

            current["registrationCount"] += 1

            if last_used > current["lastUsedAt"]:
                record["registrationCount"] = (
                    current["registrationCount"]
                )

                medicines[key] = record

    # ---------------------------------
    # 2. 복약 기록에서 추가 조회
    #
    # schedules에서 삭제된 약이라도
    # logs에 정보가 남아 있으면 표시
    # ---------------------------------

    if category != "stock":
        for snap in _read_collection("logs", user_id):
            data = snap.to_dict() or {}

            # 상비약 사용 로그 제외
            if (
                data.get("stockId")
                or data.get("isStock") is True
            ):
                continue

            name = _text(
                data.get("medicineName")
            )

            if not name:
                continue

            item_seq = (
                _text(data.get("itemSeq")) or None
            )

            key = _medicine_key(name, item_seq)

            last_used = _date_string(
                data.get("takenAt")
                or data.get("date")
                or data.get("createdAt")
            )

            if key not in medicines:
                medicines[key] = {
                    "medicineName": name,
                    "itemSeq": item_seq,
                    "category": "prescription",
                    "lastUsedAt": last_used,
                    "registrationCount": 0,
                    "scheduleId": (
                        data.get("scheduleId")
                    ),
                    "source": "log",
                    "requiresDosageConfirmation": True,
                }

            elif last_used > medicines[key]["lastUsedAt"]:
                medicines[key]["lastUsedAt"] = (
                    last_used
                )

    results = list(medicines.values())

    results.sort(
        key=lambda item: item["lastUsedAt"],
        reverse=True,
    )

    return {
        "success": True,
        "count": min(len(results), limit),
        "medicines": results[:limit],
    }
