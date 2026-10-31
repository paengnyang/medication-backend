
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from firebase_config import db


CACHE_COLLECTION = "medicineDetailCache"
CACHE_DAYS = 7


def get_cached_medicine_detail(
    item_seq: str,
) -> Optional[dict]:
    """유효기간이 남은 의약품 상세정보 캐시 조회."""

    item_seq = str(item_seq or "").strip()

    if not item_seq:
        return None

    try:
        snapshot = (
            db.collection(CACHE_COLLECTION)
            .document(item_seq)
            .get()
        )

        if not snapshot.exists:
            print(f"[DETAIL CACHE] MISS: {item_seq}")
            return None

        cached = snapshot.to_dict() or {}

        expires_at = cached.get("expiresAt")
        detail = cached.get("detail")

        if not isinstance(detail, dict) or not detail:
            return None

        if not isinstance(expires_at, datetime):
            return None

        now = datetime.now(timezone.utc)

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(
                tzinfo=timezone.utc
            )

        if expires_at <= now:
            print(f"[DETAIL CACHE] EXPIRED: {item_seq}")
            return None

        print(f"[DETAIL CACHE] HIT: {item_seq}")
        return detail.copy()

    except Exception as e:
        print(f"[DETAIL CACHE] 읽기 오류: {e}")
        return None


def save_medicine_detail_cache(
    item_seq: str,
    detail: dict,
) -> None:
    """정상 조회한 의약품 상세정보만 저장."""

    item_seq = str(item_seq or "").strip()

    if not item_seq or not isinstance(detail, dict):
        return

    if not detail:
        return

    now = datetime.now(timezone.utc)

    try:
        (
            db.collection(CACHE_COLLECTION)
            .document(item_seq)
            .set({
                "itemSeq": item_seq,
                "detail": detail,
                "updatedAt": now,
                "expiresAt": now + timedelta(
                    days=CACHE_DAYS
                ),
            })
        )

        print(f"[DETAIL CACHE] SAVED: {item_seq}")

    except Exception as e:
        # 캐시 저장 실패가 원본 의약품 조회를
        # 실패로 바꾸지 않도록 처리
        print(f"[DETAIL CACHE] 저장 오류: {e}")


def fetch_medicine_detail_cached(
    item_seq: str,
    fetcher: Callable[[str], Optional[dict]],
) -> Optional[dict]:
    """
    캐시 우선 조회.
    캐시에 없으면 기존 상세정보 조회 함수 실행.

    fetcher는 기존 식약처 API 조회 함수.
    """

    item_seq = str(item_seq or "").strip()

    cached = get_cached_medicine_detail(item_seq)

    if cached is not None:
        return cached

    # 기존 외부 API 조회 로직 그대로 사용
    detail = fetcher(item_seq)

    # 정상적인 상세정보가 아닐 경우 캐싱하지 않음
    if not isinstance(detail, dict) or not detail:
        return detail

    save_medicine_detail_cache(
        item_seq=item_seq,
        detail=detail,
    )

    return detail
