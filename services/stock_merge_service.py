
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

from google.cloud import firestore

from firebase_config import db


KST = ZoneInfo("Asia/Seoul")


def _now():
    return datetime.now(KST).isoformat()


def _item_seq(value):
    return str(value or "").strip()


def _stock_key(user_id, item_seq):
    value = f"{user_id}|{item_seq}"
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def save_or_merge_stock(
    user_id: str,
    medicine_name: str,
    item_seq: str,
    add_quantity: int,
    *,
    single_dose_text: str = "",
    max_daily_dose_text: str = "",
    usage: str = "",
    allergy_warning=None,
):
    """
    동일 사용자 + itemSeq 상비약 통합 저장.

    - 기존 문서 발견: 남은 수량에 추가
    - 없으면: 새 문서 생성
    - 기존 문서 수량 변경은 트랜잭션 사용
    """

    user_id = str(user_id or "").strip()
    medicine_name = str(medicine_name or "").strip()
    item_seq = _item_seq(item_seq)

    try:
        add_quantity = int(add_quantity)
    except (ValueError, TypeError):
        raise ValueError("추가 수량은 정수여야 합니다.")

    if not user_id or not medicine_name:
        raise ValueError("사용자 또는 의약품명이 없습니다.")

    if not item_seq:
        raise ValueError(
            "품목기준코드(itemSeq)를 먼저 확정해주세요."
        )

    if add_quantity <= 0:
        raise ValueError("추가 수량은 1 이상이어야 합니다.")

    collection = db.collection("stockMedicines")

    # 기존 무작위 문서 ID도 검색
    docs = list(
        collection
        .where("userId", "==", user_id)
        .stream()
    )

    matches = []

    for doc in docs:
        data = doc.to_dict() or {}

        if _item_seq(data.get("itemSeq")) == item_seq:
            matches.append(doc)

    # 과거에 중복 생성된 상비약을 임의로 하나만
    # 갱신하면 수량이 분산되므로 수동 통합 요청
    if len(matches) > 1:
        raise ValueError(
            "동일 품목의 기존 상비약이 여러 개입니다. "
            "중복 재고를 먼저 통합해주세요. "
            "문서 ID: " +
            ", ".join(doc.id for doc in matches)
        )

    # 기존 문서가 있으면 그 ID를 유지
    # 없으면 새 등록 경로의 고정 문서 ID 사용
    if matches:
        stock_ref = matches[0].reference
    else:
        stock_ref = collection.document(
            _stock_key(user_id, item_seq)
        )

    @firestore.transactional
    def _write(transaction):
        snapshot = stock_ref.get(transaction=transaction)

        if snapshot.exists:
            current = snapshot.to_dict() or {}

            # 같은 ID에 다른 사용자/품목이 들어 있으면 차단
            if (
                str(current.get("userId") or "") != user_id
                or _item_seq(current.get("itemSeq"))
                != item_seq
            ):
                raise ValueError(
                    "상비약 문서의 사용자 또는 품목코드가 일치하지 않습니다."
                )

            try:
                remaining = int(
                    float(current.get("remainingCount", 0))
                )
            except (ValueError, TypeError):
                raise ValueError(
                    "기존 잔여 수량이 올바르지 않습니다."
                )

            new_remaining = remaining + add_quantity

            updates = {
                "remainingCount": new_remaining,
                "setSize": add_quantity,
                "isStock": True,
                "asNeeded": True,
                "itemSeq": item_seq,
                "updatedAt": _now(),
            }

            if single_dose_text:
                updates["singleDoseText"] = single_dose_text

            if max_daily_dose_text:
                updates["maxDailyDoseText"] = max_daily_dose_text

            if usage:
                updates["usage"] = usage

            if allergy_warning:
                updates["allergyWarning"] = allergy_warning

            transaction.update(stock_ref, updates)

            result = {
                **current,
                **updates,
                "stockId": stock_ref.id,
                "userId": user_id,
                "medicineName": current.get(
                    "medicineName"
                ) or medicine_name,
            }

            return result, True

        stock_data = {
            "stockId": stock_ref.id,
            "userId": user_id,
            "medicineName": medicine_name,
            "itemSeq": item_seq,
            "remainingCount": add_quantity,
            "setSize": add_quantity,
            "isStock": True,
            "asNeeded": True,
            "singleDoseText": single_dose_text,
            "maxDailyDoseText": max_daily_dose_text,
            "usage": usage,
            "createdAt": _now(),
            "updatedAt": _now(),
        }

        if allergy_warning:
            stock_data["allergyWarning"] = allergy_warning

        transaction.set(stock_ref, stock_data)
        return stock_data, False

    transaction = db.transaction()
    stock_data, merged = _write(transaction)

    return {
        "success": True,
        "merged": merged,
        "created": not merged,
        "addedCount": add_quantity,
        "remainingCount": stock_data["remainingCount"],
        "stockId": stock_ref.id,
        "stockData": stock_data,
    }


def reuse_or_merge_stock(
    user_id: str,
    medicine_name: str,
    item_seq: str,
    quantity: int,
    request_id: str,
    source_id: str,
    medicine_fields: dict = None,
):
    """
    빠른 재등록 전용 상비약 합산.

    같은 requestId는 정확히 한 번만 처리한다.
    기존 stockMedicines 문서가 있으면 재고를 합산한다.
    """

    user_id = str(user_id or "").strip()
    item_seq = str(item_seq or "").strip()
    request_id = str(request_id or "").strip()

    if not user_id or not item_seq or not request_id:
        raise ValueError(
            "사용자, 품목코드, 요청 ID가 필요합니다."
        )

    if not medicine_name.strip():
        raise ValueError("의약품명이 필요합니다.")

    quantity = int(quantity)

    if quantity <= 0:
        raise ValueError("추가 수량은 1 이상이어야 합니다.")

    extra = dict(medicine_fields or {})

    # 클라이언트가 임의 필드를 덮어쓰지 못하도록 제한
    extra = {
        k: v for k, v in extra.items()
        if k in ("manufacturer", "ingredient", "category")
        and v is not None
    }

    stocks = db.collection("stockMedicines")

    # 요청 ID별 처리 기록
    request_key = hashlib.sha256(
        f"{user_id}|{request_id}".encode("utf-8")
    ).hexdigest()

    request_ref = (
        db.collection("medicineStockReuseRequests")
        .document(request_key)
    )

    # 요청 재시도라면 저장된 결과를 그대로 반환
    old_request = request_ref.get()

    if old_request.exists:
        old = old_request.to_dict() or {}

        if (
            old.get("itemSeq") != item_seq
            or old.get("sourceId") != source_id
            or old.get("quantity") != quantity
        ):
            raise ValueError(
                "이미 다른 내용으로 사용한 requestId입니다."
            )

        return {
            **old["result"],
            "created": False,
            "alreadyProcessed": True,
        }

    # 기존 데이터는 무작위 문서 ID일 수 있으므로 조회
    docs = list(
        stocks.where("userId", "==", user_id).stream()
    )

    matches = [
        doc for doc in docs
        if str(
            (doc.to_dict() or {}).get("itemSeq") or ""
        ).strip() == item_seq
    ]

    if len(matches) > 1:
        raise ValueError(
            "동일한 상비약 문서가 여러 개 있습니다. "
            "기존 중복 재고를 먼저 정리해주세요."
        )

    if matches:
        stock_ref = matches[0].reference
    else:
        stock_ref = stocks.document(
            _stock_key(user_id, item_seq)
        )

    @firestore.transactional
    def _save(transaction):
        request_snap = request_ref.get(
            transaction=transaction
        )

        if request_snap.exists:
            old = request_snap.to_dict() or {}

            if (
                old.get("itemSeq") != item_seq
                or old.get("sourceId") != source_id
                or old.get("quantity") != quantity
            ):
                raise ValueError(
                    "이미 다른 내용으로 사용한 requestId입니다."
                )

            return {
                **old["result"],
                "created": False,
                "alreadyProcessed": True,
            }

        stock_snap = stock_ref.get(
            transaction=transaction
        )

        if stock_snap.exists:
            current = stock_snap.to_dict() or {}

            if (
                current.get("userId") != user_id
                or str(
                    current.get("itemSeq") or ""
                ).strip() != item_seq
            ):
                raise ValueError(
                    "기존 상비약 사용자 또는 품목코드가 다릅니다."
                )

            remaining = int(
                current.get("remainingCount", 0)
            )

            new_remaining = remaining + quantity

            transaction.update(
                stock_ref,
                {
                    "remainingCount": new_remaining,
                    "setSize": quantity,
                    "updatedAt": _now(),
                },
            )

            created = False

        else:
            new_remaining = quantity

            stock_data = {
                **extra,
                "stockId": stock_ref.id,
                "userId": user_id,
                "medicineName": medicine_name,
                "itemSeq": item_seq,
                "isStock": True,
                "asNeeded": True,
                "remainingCount": new_remaining,
                "setSize": quantity,
                "active": True,
                "createdAt": _now(),
                "updatedAt": _now(),
                "registrationSource": "medicine_reuse",
                "sourceId": source_id,
            }

            transaction.set(stock_ref, stock_data)
            created = True

        result = {
            "success": True,
            "created": created,
            "alreadyProcessed": False,
            "merged": not created,
            "collection": "stockMedicines",
            "documentId": stock_ref.id,
            "stockId": stock_ref.id,
            "itemSeq": item_seq,
            "medicineName": medicine_name,
            "addedCount": quantity,
            "remainingCount": new_remaining,
        }

        transaction.set(
            request_ref,
            {
                "userId": user_id,
                "itemSeq": item_seq,
                "sourceId": source_id,
                "quantity": quantity,
                "createdAt": _now(),
                "result": result,
            },
        )

        return result

    return _save(db.transaction())
