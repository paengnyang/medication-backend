
import hashlib
import re
import uuid
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Literal
from datetime import timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field, model_validator
from google.cloud import firestore

from firebase_config import db
from services.allergy_service import check_allergy
from services.medicine_duplicate_service import find_duplicate_schedule
from services.stock_merge_service import reuse_or_merge_stock

router = APIRouter(
    prefix="/medicine-reuse",
    tags=["Medicine Reuse"],
)

KST = ZoneInfo("Asia/Seoul")

SOURCES = {
    "recent": ("schedules", "stockMedicines"),
    "favorite": ("medicineFavorites",),
}

MEDICINE_FIELDS = (
    "medicineName",
    "itemSeq",
    "manufacturer",
    "ingredient",
    "category",
)


def now_iso():
    return datetime.now(KST).isoformat()


def clean(value):
    return str(value or "").strip()


def medicine_key(user_id, item_seq, mode):
    raw = f"{user_id}|{item_seq}|{mode}"
    return hashlib.sha256(raw.encode()).hexdigest()


def medicine_info(data):
    result = {
        key: data.get(key)
        for key in MEDICINE_FIELDS
        if data.get(key) is not None
    }
    return result


def load_source(user_id, source_type, source_id):
    if source_type not in ("recent", "favorite"):
        raise HTTPException(400, "잘못된 sourceType입니다.")

    if not clean(user_id) or not clean(source_id):
        raise HTTPException(422, "userId와 sourceId가 필요합니다.")

    collections = SOURCES[source_type]

    for collection in collections:
        doc = db.collection(collection).document(source_id).get()

        if not doc.exists:
            continue

        data = doc.to_dict() or {}

        if clean(data.get("userId")) != user_id:
            raise HTTPException(404, "해당 의약품을 찾을 수 없습니다.")

        if not clean(data.get("itemSeq")):
            raise HTTPException(
                422, "품목기준코드가 없어 재등록할 수 없습니다."
            )

        return collection, data

    raise HTTPException(404, "기존 의약품 정보를 찾을 수 없습니다.")


def validate_allergy(user_id, item_seq):
    try:
        result = check_allergy(
            user_id=user_id,
            item_seq=item_seq,
        )
    except Exception:
        raise HTTPException(
            503, "알레르기 검사에 실패했습니다."
        )

    if not result.get("success"):
        raise HTTPException(
            503, "알레르기 정보를 조회할 수 없습니다."
        )

    if result.get("checked") is not True:
        raise HTTPException(
            409, "성분 확인이 완료되지 않아 재등록할 수 없습니다."
        )

    if result.get("hasWarning"):
        raise HTTPException(
            409, "등록된 알레르기 성분과 일치하여 재등록할 수 없습니다."
        )

    return {
        "status": result.get("screeningStatus"),
        "checked": True,
        "hasWarning": False,
    }


def is_active(data, mode, today):
    if mode == "stock":
        return True

    if data.get("active") is False:
        return False

    end_date = clean(data.get("endDate"))
    if end_date and end_date < today:
        return False

    return True


class SourceRequest(BaseModel):
    userId: str = Field(min_length=1)
    sourceType: Literal["recent", "favorite"]
    sourceId: str


class RegisterRequest(SourceRequest):
    mode: Literal["schedule", "stock"]

    # 정기복용약: 복용 시작/종료 날짜, 하루 복용 횟수와 시간
    startDate: date | None = None
    endDate: date | None = None
    dailyCount: int | None = Field(default=None, ge=1, le=12)
    times: list[str] = Field(default_factory=list)

    # 두 유형 모두 재고 수량을 새로 확인
    quantity: int = Field(ge=1)
    remainingCount: int = Field(ge=0)

    # 요청 재시도 시 동일 등록으로 식별
    requestId: str = Field(min_length=8, max_length=100)

    @model_validator(mode="after")
    def validate_registration(self):
        if self.remainingCount > self.quantity:
            raise ValueError("남은 수량은 전체 수량보다 클 수 없습니다.")

        if self.mode == "schedule":
            if not self.startDate or not self.endDate:
                raise ValueError("복약 시작일과 종료일이 필요합니다.")

            if self.endDate < self.startDate:
                raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")

            if self.endDate < datetime.now(KST).date():
                raise ValueError("이미 종료된 일정은 등록할 수 없습니다.")

            if self.dailyCount is None:
                raise ValueError("하루 복용 횟수가 필요합니다.")

            if len(self.times) != self.dailyCount:
                raise ValueError("복용 시간 수와 하루 복용 횟수가 다릅니다.")

            for value in self.times:
                if not re.fullmatch(
                    r"(?:[01]\d|2[0-3]):[0-5]\d",
                    value,
                ):
                    raise ValueError("복용 시간은 HH:MM 형식이어야 합니다.")

            if len(set(self.times)) != len(self.times):
                raise ValueError("중복된 복용 시간이 있습니다.")

        return self



def _parse_datetime(value, fallback_time="00:00"):
    """Firestore Timestamp, ISO 문자열, 날짜 문자열 처리."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, date):
        dt = datetime.combine(
            value, datetime.min.time()
        )
    elif value:
        text = str(value).strip().replace("Z", "+00:00")

        try:
            if len(text) == 10:
                text += f"T{fallback_time}"
            dt = datetime.fromisoformat(text)
        except (TypeError, ValueError):
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=KST)

    return dt.astimezone(timezone.utc)


def _registration_date(data):
    for field in ("createdAt", "updatedAt", "startDate"):
        dt = _parse_datetime(data.get(field))
        if dt:
            return dt
    return None


def _log_date(data):
    # 실제 복용 시각 우선
    for field in ("takenAt", "consumedAt", "completedAt"):
        dt = _parse_datetime(data.get(field))
        if dt:
            return dt

    # 기존 logs: date + time
    log_date = clean(data.get("date"))
    log_time = clean(data.get("time")) or "00:00"

    if log_date:
        dt = _parse_datetime(log_date, log_time)
        if dt:
            return dt

    return _parse_datetime(data.get("createdAt"))


def _is_taken_log(data):
    # 명시적으로 미복용된 기록은 제외
    if data.get("taken") is False:
        return False

    if data.get("completed") is False:
        return False

    status = clean(data.get("status")).lower()
    if status in (
        "missed", "skipped", "pending",
        "not_taken", "cancelled",
    ):
        return False

    event_type = clean(data.get("type")).lower()
    if event_type in (
        "missed", "reminder", "notification",
    ):
        return False

    # 복용 여부가 명확한 기록만 사용
    return (
        data.get("taken") is True
        or data.get("completed") is True
        or status in ("taken", "completed", "done")
        or event_type in ("consume_stock", "take")
    )


@router.get("/options")
def get_reuse_options(
    userId: str = Query(min_length=1),
):
    user_id = userId.strip()

    # --------------------------------------------------
    # 1. 등록된 일정과 상비약 수집
    # --------------------------------------------------
    recent_by_item = {}
    id_to_item = {}
    name_to_item = {}

    for collection in ("schedules", "stockMedicines"):
        docs = (
            db.collection(collection)
            .where("userId", "==", user_id)
            .stream()
        )

        for doc in docs:
            data = doc.to_dict() or {}

            item_seq = clean(data.get("itemSeq"))
            name = clean(data.get("medicineName"))

            # itemSeq가 없는 약은 정확한 재등록 불가
            if not item_seq:
                continue

            registered_at = _registration_date(data)

            candidate = {
                "sourceType": "recent",
                "sourceCollection": collection,
                "sourceId": doc.id,
                **medicine_info(data),
                "lastTakenAt": None,
                "lastRegisteredAt": (
                    registered_at.isoformat()
                    if registered_at else None
                ),
                "usageCount": 0,
                "_registeredDt": registered_at,
                "_lastTakenDt": None,
            }

            # 같은 약의 모든 일정 ID를 연결
            id_to_item[doc.id] = item_seq

            # itemSeq가 없는 과거 로그에 대비
            if name:
                name_key = "".join(name.lower().split())
                name_to_item.setdefault(
                    name_key, set()
                ).add(item_seq)

            previous = recent_by_item.get(item_seq)

            # 대표 sourceId는 가장 최근 등록 건 선택
            if previous is None:
                recent_by_item[item_seq] = candidate

            elif (
                registered_at or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ) > (
                previous["_registeredDt"]
                or datetime.min.replace(
                    tzinfo=timezone.utc
                )
            ):
                # 복용 통계는 다음 단계에서 다시 합산
                recent_by_item[item_seq] = candidate

    # --------------------------------------------------
    # 2. 실제 복용 완료 기록 조회
    # --------------------------------------------------
    # 과거 버전의 logs / medicationLogs 모두 지원
    for collection in ("logs", "medicationLogs"):
        log_docs = (
            db.collection(collection)
            .where("userId", "==", user_id)
            .stream()
        )

        for log_doc in log_docs:
            log = log_doc.to_dict() or {}

            if not _is_taken_log(log):
                continue

            taken_at = _log_date(log)
            if not taken_at:
                continue

            item_seq = clean(log.get("itemSeq"))

            if item_seq not in recent_by_item:
                item_seq = ""

            # 로그와 기존 일정 ID 연결
            if not item_seq:
                schedule_id = clean(log.get("scheduleId"))
                stock_id = clean(log.get("stockId"))

                item_seq = (
                    id_to_item.get(schedule_id)
                    or id_to_item.get(stock_id)
                    or ""
                )

            # 오래된 로그의 의약품명으로 연결
            if not item_seq:
                name = clean(log.get("medicineName"))
                name_key = "".join(name.lower().split())

                possible_items = name_to_item.get(
                    name_key, set()
                )

                # 이름이 정확히 하나의 itemSeq에만 대응할 때
                if len(possible_items) == 1:
                    item_seq = next(iter(possible_items))

            if item_seq not in recent_by_item:
                continue

            entry = recent_by_item[item_seq]

            entry["usageCount"] += 1

            previous_taken = entry["_lastTakenDt"]

            if (
                previous_taken is None
                or taken_at > previous_taken
            ):
                entry["_lastTakenDt"] = taken_at
                entry["lastTakenAt"] = taken_at.isoformat()

    # --------------------------------------------------
    # 3. 실제 복용일 우선, 미복용은 등록일 기준 정렬
    # --------------------------------------------------
    minimum = datetime.min.replace(
        tzinfo=timezone.utc
    )

    recent_options = sorted(
        recent_by_item.values(),
        key=lambda entry: (
            entry["_lastTakenDt"] is not None,
            entry["_lastTakenDt"] or minimum,
            entry["_registeredDt"] or minimum,
        ),
        reverse=True,
    )

    # 내부 비교 필드 제거
    for entry in recent_options:
        entry.pop("_registeredDt", None)
        entry.pop("_lastTakenDt", None)

    # --------------------------------------------------
    # 4. 즐겨찾기는 별도 목록으로 유지
    # --------------------------------------------------
    favorite_options = []

    docs = (
        db.collection("medicineFavorites")
        .where("userId", "==", user_id)
        .stream()
    )

    seen_favorites = set()

    for doc in docs:
        data = doc.to_dict() or {}
        item_seq = clean(data.get("itemSeq"))

        if not item_seq or item_seq in seen_favorites:
            continue

        seen_favorites.add(item_seq)

        favorite_options.append({
            "sourceType": "favorite",
            "sourceCollection": "medicineFavorites",
            "sourceId": doc.id,
            **medicine_info(data),
        })

    # 기존 options 응답 구조 유지
    options = recent_options + favorite_options

    return {
        "success": True,
        "userId": user_id,
        "count": len(options),
        "recentCount": len(recent_options),
        "favoriteCount": len(favorite_options),
        "options": options,
    }



@router.post("/preview")
def preview_reuse(body: SourceRequest):
    collection, data = load_source(
        body.userId,
        body.sourceType,
        body.sourceId,
    )

    item_seq = clean(data["itemSeq"])
    allergy = validate_allergy(body.userId, item_seq)

    return {
        "success": True,
        "sourceType": body.sourceType,
        "sourceCollection": collection,
        "sourceId": body.sourceId,
        "medicine": medicine_info(data),
        "allergyCheck": allergy,
        "requiresConfirmation": [
            "mode",
            "quantity",
            "remainingCount",
            "startDate",
            "endDate",
            "dailyCount",
            "times",
        ],
        "message": (
            "기존 의약품 정보를 불러왔습니다. "
            "새 복약 일정과 수량을 확인한 뒤 등록하세요."
        ),
    }


@router.post("/register")
def register_reuse(body: RegisterRequest):
    source_collection, source = load_source(
        body.userId,
        body.sourceType,
        body.sourceId,
    )

    item_seq = clean(source["itemSeq"])

    # 기존 알레르기 검사 재사용
    allergy = validate_allergy(body.userId, item_seq)
    
    
    # ===========================================
    # 상비약 빠른 재등록: 기존 재고에 수량 합산
    # ===========================================
    if body.mode == "stock":
        try:
            result = reuse_or_merge_stock(
                user_id=body.userId,
                medicine_name=clean(
                    source.get("medicineName")
                ),
                item_seq=item_seq,
                quantity=body.quantity,
                request_id=body.requestId,
                source_id=body.sourceId,
                medicine_fields={
                    "manufacturer": source.get("manufacturer"),
                    "ingredient": source.get("ingredient"),
                    "category": source.get("category"),
                },
            )

        except ValueError as e:
            raise HTTPException(
                status_code=409,
                detail=str(e),
            )

        return result


    target_collection = (
        "schedules"
        if body.mode == "schedule"
        else "stockMedicines"
    )

    key = medicine_key(
        body.userId,
        item_seq,
        body.mode,
    )

    lock_ref = db.collection(
        "medicineReuseLocks"
    ).document(key)

    # requestId를 이용한 중복 요청 처리
    request_ref = db.collection(
        "medicineReuseRequests"
    ).document(
        hashlib.sha256(
            f"{body.userId}|{body.requestId}".encode()
        ).hexdigest()
    )

    # 동일 요청 재시도 여부 먼저 확인
    existing_request = request_ref.get()

    # 이전에 처리한 requestId는 기존 트랜잭션에서 처리
    # 새로운 등록 요청에만 복용 기간 중복 검사
    if (
        not existing_request.exists
        and body.mode == "schedule"
    ):
        try:
            duplicate = find_duplicate_schedule(
                user_id=body.userId,
                item_seq=item_seq,
                start_date=body.startDate,
                end_date=body.endDate,
            )

        except ValueError as e:
            raise HTTPException(
                status_code=422,
                detail=str(e),
            )

        if duplicate:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": (
                        "동일 의약품의 복용 기간이 "
                        "기존 일정과 겹칩니다."
                    ),
                    "existingScheduleId": duplicate[
                        "scheduleId"
                    ],
                },
            )

    doc_id = uuid.uuid4().hex
    target_ref = db.collection(
        target_collection
    ).document(doc_id)

    created_at = now_iso()
    today = datetime.now(KST).date().isoformat()

    new_data = {
        **medicine_info(source),
        "userId": body.userId,
        "itemSeq": item_seq,
        "medicineName": clean(source.get("medicineName")),
        "isStock": body.mode == "stock",
        "quantity": body.quantity,
        "remainingCount": body.remainingCount,
        "active": True,
        "createdAt": created_at,
        "updatedAt": created_at,
        "registrationSource": "medicine_reuse",
        "sourceCollection": source_collection,
        "sourceId": body.sourceId,
    }

    if body.mode == "schedule":
        new_data.update({
            "startDate": body.startDate.isoformat(),
            "endDate": body.endDate.isoformat(),
            "dailyCount": body.dailyCount,
            "times": sorted(body.times),
            "asNeeded": False,
        })
    else:
        new_data["asNeeded"] = True

    transaction = db.transaction()

    @firestore.transactional
    def commit_registration(transaction):
        # 같은 요청을 재전송하면 기존 결과 반환
        request_snapshot = request_ref.get(transaction=transaction)

        if request_snapshot.exists:
            saved = request_snapshot.to_dict() or {}
            if (
                saved.get("userId") != body.userId
                or saved.get("itemSeq") != item_seq
                or saved.get("mode") != body.mode
            ):
                return {"status": "request_conflict"}

            return {
                "status": "already_processed",
                "documentId": saved.get("documentId"),
            }

        lock_snapshot = lock_ref.get(transaction=transaction)

        if lock_snapshot.exists:
            lock = lock_snapshot.to_dict() or {}
            previous_id = lock.get("documentId")

            if previous_id:
                previous_ref = db.collection(
                    target_collection
                ).document(previous_id)

                previous_snapshot = previous_ref.get(
                    transaction=transaction
                )

                
                previous = (
                    previous_snapshot.to_dict() or {}
                    if previous_snapshot.exists
                    else {}
                )

                previous_active = (
                    previous_snapshot.exists
                    and previous.get("active") is not False
                )

                if body.mode == "schedule":
                    # 기간 중복 여부는 위에서 검사한 것과
                    # 동일한 규칙을 적용
                    from services.medicine_duplicate_service import _date

                    old_start = _date(
                        previous.get("startDate")
                    )
                    old_end = _date(
                        previous.get("endDate")
                    )

                    if old_start and old_end:
                        period_overlaps = (
                            body.startDate <= old_end
                            and old_start <= body.endDate
                        )
                    else:
                        # 기존 기간을 알 수 없으면 중복 의심
                        period_overlaps = True

                    previous_active = (
                        previous_active and period_overlaps
                    )

                if previous_active:

                    return {
                        "status": "duplicate",
                        "documentId": previous_id,
                    }

        transaction.set(target_ref, new_data)

        transaction.set(lock_ref, {
            "userId": body.userId,
            "itemSeq": item_seq,
            "mode": body.mode,
            "documentId": doc_id,
            "updatedAt": created_at,
        })

        transaction.set(request_ref, {
            "userId": body.userId,
            "itemSeq": item_seq,
            "mode": body.mode,
            "documentId": doc_id,
            "createdAt": created_at,
        })

        return {
            "status": "created",
            "documentId": doc_id,
        }

    result = commit_registration(transaction)

    if result["status"] == "request_conflict":
        raise HTTPException(
            409, "이미 다른 등록에 사용된 requestId입니다."
        )

    if result["status"] == "duplicate":
        raise HTTPException(
            409,
            detail={
                "message": "현재 등록된 동일 의약품이 있습니다.",
                "documentId": result["documentId"],
            },
        )

    return {
        "success": True,
        "created": result["status"] == "created",
        "alreadyProcessed": (
            result["status"] == "already_processed"
        ),
        "collection": target_collection,
        "documentId": result["documentId"],
        "itemSeq": item_seq,
        "medicineName": new_data["medicineName"],
        "allergyCheck": allergy,
        "message": "의약품 재등록 요청이 처리되었습니다.",
    }
