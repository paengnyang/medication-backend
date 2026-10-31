
from datetime import date

from firebase_config import db


def _text(value):
    return str(value or "").strip()


def _date(value):
    if isinstance(value, date):
        return value

    try:
        return date.fromisoformat(_text(value)[:10])
    except (TypeError, ValueError):
        return None


def find_duplicate_schedule(
    user_id,
    item_seq,
    start_date,
    end_date,
    exclude_id=None,
):
    """
    동일 사용자 + 동일 품목기준코드 + 겹치는 복용 기간 검색.

    반환:
        None: 중복 없음
        dict: 중복 일정 발견
    """

    item_seq = _text(item_seq)

    new_start = _date(start_date)
    new_end = _date(end_date)

    if not item_seq:
        raise ValueError(
            "중복 검사를 위해 품목기준코드(itemSeq)가 필요합니다."
        )

    if not new_start or not new_end:
        raise ValueError(
            "복용 시작일과 종료일이 필요합니다."
        )

    if new_end < new_start:
        raise ValueError(
            "종료일은 시작일보다 빠를 수 없습니다."
        )

    docs = (
        db.collection("schedules")
        .where("userId", "==", user_id)
        .stream()
    )

    for doc in docs:
        if doc.id == exclude_id:
            continue

        existing = doc.to_dict() or {}

        if existing.get("active") is False:
            continue

        if existing.get("isStock") is True:
            continue

        if _text(existing.get("itemSeq")) != item_seq:
            continue

        old_start = _date(existing.get("startDate"))
        old_end = _date(existing.get("endDate"))

        # 기존 일정에 날짜가 없다면 안전하게 중복 의심 처리
        if not old_start or not old_end:
            return {
                "scheduleId": doc.id,
                "medicineName": existing.get("medicineName", ""),
                "reason": "기존 일정의 복용 기간 확인 불가",
            }

        # 날짜 구간 겹침 검사
        if (
            new_start <= old_end
            and old_start <= new_end
        ):
            return {
                "scheduleId": doc.id,
                "medicineName": existing.get("medicineName", ""),
                "startDate": old_start.isoformat(),
                "endDate": old_end.isoformat(),
                "reason": "동일 의약품의 복용 기간 중복",
            }

    return None
