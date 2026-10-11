from fastapi import APIRouter, HTTPException
from datetime import (datetime, timedelta, timezone)
import uuid

from firebase_config import db
from models.schemas import (
    StockCreateRequest,
    StockConsumeRequest,
    StockAddSetRequest,
)


router = APIRouter(
    prefix="/stocks",
    tags=["Stocks"]
)

@router.delete(
    "/{user_id}/{stock_id}"
)
def delete_stock(
    user_id: str,
    stock_id: str,
):
    # =================================
    # 상비약 확인
    # =================================

    ref = (
        db.collection(
            "stockMedicines"
        )
        .document(
            stock_id
        )
    )

    doc = ref.get()

    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail=
                "상비약을 찾을 수 없습니다.",
        )

    stock = (
        doc.to_dict()
        or {}
    )

    # =================================
    # 사용자 확인
    # =================================

    if (
        stock.get("userId")
        != user_id
    ):
        raise HTTPException(
            status_code=403,
            detail=
                "접근 권한이 없습니다.",
        )

    # =================================
    # 해당 상비약의 복용 로그 삭제
    # =================================

    log_docs = (
        db.collection("logs")
        .where(
            "userId",
            "==",
            user_id,
        )
        .where(
            "stockId",
            "==",
            stock_id,
        )
        .stream()
    )

    deleted_log_count = 0

    for log_doc in log_docs:
        log_data = (
            log_doc.to_dict()
            or {}
        )

        # 일반 복약 기록은 보호
        if (
            log_data.get("isStock")
            is not True
        ):
            continue

        log_doc.reference.delete()

        deleted_log_count += 1

    # =================================
    # 상비약 자체 삭제
    # =================================

    ref.delete()

    return {
        "success": True,

        "message":
            "상비약과 관련 복용 기록이 "
            "삭제되었습니다.",

        "stockId":
            stock_id,

        "deletedLogCount":
            deleted_log_count,
    }

# 상비약 추가
@router.post("/add")
def add_stock(data: StockCreateRequest):

    # 같은 사용자 + 같은 약이 이미 있는지 확인
    docs = list(
        db.collection("stockMedicines")
        .where("userId", "==", data.userId)
        .where("medicineName", "==", data.medicineName)
        .stream()
    )

    # 이미 있으면 재고 추가
    if docs:
        doc = docs[0]
        current = doc.to_dict()

        new_remaining = (
            current.get("remainingCount", 0)
            + data.remainingCount
        )

        doc.reference.update({
            "remainingCount": new_remaining,
            "setSize": data.setSize,
            "updatedAt": datetime.now().isoformat(),
        })

        return {
            "success": True,
            "message": "상비약 재고 추가 완료",
            "stockId": doc.id,
            "remainingCount": new_remaining,
        }

    # 처음 등록하는 약
    stock_id = str(uuid.uuid4())

    stock_data = {
        "stockId": stock_id,
        "userId": data.userId,
        "medicineName": data.medicineName,
        "remainingCount": data.remainingCount,
        "setSize": data.setSize,
        "createdAt": datetime.now().isoformat(),
    }

    db.collection("stockMedicines").document(stock_id).set(
        stock_data
    )

    return {
        "success": True,
        "message": "상비약 등록 완료",
        "stock": stock_data,
    }


# 로그인 사용자의 상비약 조회
@router.get("/user/{user_id}")
def get_user_stocks(user_id: str):

    docs = (
        db.collection("stockMedicines")
        .where("userId", "==", user_id)
        .stream()
    )

    stocks = []

    for doc in docs:
        stocks.append(doc.to_dict())

    return {
        "success": True,
        "count": len(stocks),
        "stocks": stocks,
    }


@router.post("/consume")
def consume_stock(
    data: StockConsumeRequest
):

    ref = (
        db.collection(
            "stockMedicines"
        )
        .document(
            data.stockId
        )
    )

    doc = ref.get()

    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail=
                "상비약을 찾을 수 없습니다."
        )

    stock = doc.to_dict()

    # =================================
    # 사용자 확인
    # =================================

    if stock.get(
        "userId"
    ) != data.userId:
        raise HTTPException(
            status_code=403,
            detail=
                "접근 권한이 없습니다."
        )

    remaining = int(
        stock.get(
            "remainingCount",
            0,
        )
    )

    if remaining <= 0:
        return {
            "success":
                False,

            "message":
                "남은 약이 없습니다.",

            "remainingCount":
                0,
        }

    # =================================
    # 약 DB에서 얻은 최소 복용 간격
    # =================================

    min_interval_hours = int(
        stock.get(
            "minIntervalHours",
            0,
        )
        or 0
    )

    now_utc = datetime.now(
        timezone.utc
    )

    # =================================
    # 이전 복용 후 아직 간격이
    # 지나지 않았는지 검사
    # =================================

    next_allowed_raw = stock.get(
        "nextAllowedAt"
    )

    if (
        min_interval_hours > 0
        and next_allowed_raw
    ):
        try:
            raw_text = str(
                next_allowed_raw
            ).replace(
                "Z",
                "+00:00",
            )

            next_allowed = (
                datetime
                .fromisoformat(
                    raw_text
                )
            )

            # 과거 데이터가
            # timezone 없는 형식인 경우
            if (
                next_allowed.tzinfo
                is None
            ):
                next_allowed = (
                    next_allowed.replace(
                        tzinfo=timezone.utc
                    )
                )

            next_allowed_utc = (
                next_allowed
                .astimezone(
                    timezone.utc
                )
            )

            if (
                now_utc
                < next_allowed_utc
            ):
                seconds_left = int(
                    (
                        next_allowed_utc
                        - now_utc
                    )
                    .total_seconds()
                )

                return {
                    "success":
                        False,

                    "message":
                        "아직 다음 복용 가능 시간이 아닙니다.",

                    "remainingCount":
                        remaining,

                    "minIntervalHours":
                        min_interval_hours,

                    "nextAllowedAt":
                        next_allowed_utc
                        .isoformat(),

                    "secondsUntilNext":
                        seconds_left,
                }

        except (
            ValueError,
            TypeError,
        ):
            # 잘못된 예전 데이터면
            # 복용 자체를 막지는 않음
            pass

    # =================================
    # 복용 처리
    # =================================

    new_remaining = (
        remaining - 1
    )

    next_allowed_at = None

    if min_interval_hours > 0:
        next_allowed_at = (
            now_utc
            + timedelta(
                hours=
                    min_interval_hours
            )
        )

    update_data = {
        "remainingCount":
            new_remaining,

        "lastTakenAt":
            now_utc.isoformat(),

        "updatedAt":
            now_utc.isoformat(),
    }

    if next_allowed_at:
        update_data[
            "nextAllowedAt"
        ] = (
            next_allowed_at
            .isoformat()
        )

    else:
        update_data[
            "nextAllowedAt"
        ] = None

    ref.update(
        update_data
    )

    # =================================
    # 복용 로그
    # =================================

    # 로그 화면의 날짜/시간은
    # 한국 시간으로 저장
    kst = timezone(
        timedelta(
            hours=9
        )
    )

    now_kst = (
        now_utc
        .astimezone(kst)
    )

    log_ref = (
        db.collection(
            "logs"
        )
        .document()
    )

    log_data = {
        "logId":
            log_ref.id,
        "userId":
            data.userId,

        "stockId":
            data.stockId,

        "medicineName":
            stock.get(
                "medicineName",
                "",
            ),

        "date":
            now_kst.strftime(
                "%Y-%m-%d"
            ),

        "time":
            now_kst.strftime(
                "%H:%M"
            ),

        "taken":
            True,

        "isStock":
            True,

        "asNeeded":
            True,

        "takenAt":
            now_utc.isoformat(),

        "minIntervalHours":
            min_interval_hours,

        "nextAllowedAt":
            (
                next_allowed_at
                .isoformat()
                if next_allowed_at
                else None
            ),
    }

    log_ref.set(
        log_data
    )

    return {
        "success":
            True,

        "message":
            "상비약 복용 완료",

        "stockId":
            data.stockId,

        "remainingCount":
            new_remaining,

        "minIntervalHours":
            min_interval_hours,

        "lastTakenAt":
            now_utc.isoformat(),

        "nextAllowedAt":
            (
                next_allowed_at
                .isoformat()
                if next_allowed_at
                else None
            ),
    }


# 한 판 추가
@router.post("/add-set")
def add_stock_set(data: StockAddSetRequest):

    ref = db.collection("stockMedicines").document(
        data.stockId
    )

    doc = ref.get()

    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail="상비약을 찾을 수 없습니다."
        )

    stock = doc.to_dict()

    if stock.get("userId") != data.userId:
        raise HTTPException(
            status_code=403,
            detail="접근 권한이 없습니다."
        )

    remaining = stock.get("remainingCount", 0)
    set_size = stock.get("setSize", 0)

    new_remaining = remaining + set_size

    ref.update({
        "remainingCount": new_remaining,
    })

    return {
        "success": True,
        "remainingCount": new_remaining,
    }