from fastapi import APIRouter, HTTPException
from datetime import datetime

from firebase_config import db
from models.schemas import TakenLogRequest


# log 관련 API를 모아두는 라우터
router = APIRouter(
    prefix="/logs",
    tags=["Logs"]
)


# 복용 완료 처리 API
@router.post("/taken")
def mark_as_taken(data: TakenLogRequest):
    # 0. 중복 복용 기록 확인
    # 같은 사용자, 같은 복약 일정, 같은 날짜, 같은 시간에 이미 복용 기록이 있는지 확인
    existing_logs = (
        db.collection("medicationLogs")
        .where("userId", "==", data.userId)
        .where("scheduleId", "==", data.scheduleId)
        .where("date", "==", data.date)
        .where("time", "==", data.time)
        .stream()
    )

    for log in existing_logs:
        existing_log_data = log.to_dict()

        return {
            "success": False,
            "message": "이미 복용 완료된 기록입니다.",
            "logId": log.id,
            "log": existing_log_data
        }
    # 1. 복용 기록 저장
    log_ref = db.collection("medicationLogs").document()

    log_data = {
        "userId": data.userId,
        "scheduleId": data.scheduleId,
        "medicineName": data.medicineName,
        "date": data.date,
        "time": data.time,
        "taken": True,
        "takenAt": datetime.now().isoformat()
    }

    log_ref.set(log_data)

    # 2. 복약 일정에서 remainingCount 감소
    schedule_ref = db.collection("schedules").document(data.scheduleId)
    schedule_doc = schedule_ref.get()

    if schedule_doc.exists:
        schedule = schedule_doc.to_dict()

        current_remaining = schedule.get("remainingCount", 0)
        dosage = schedule.get("dosage", 1)

        # 남은 약 개수가 음수가 되지 않도록 max 사용
        new_remaining = max(current_remaining - dosage, 0)

        schedule_ref.update({
            "remainingCount": new_remaining
        })
        # 3. 약 부족 알림 판단
        low_stock_warning = None

        if new_remaining == 0:
            low_stock_warning = "약이 모두 소진되었습니다. 재처방 또는 구매가 필요합니다."
        elif new_remaining <= 3:
            low_stock_warning = "약이 부족합니다. 남은 약 개수를 확인해주세요."


        response = {
            "success": True,
            "message": "복용 기록 저장 완료",
            "logId": log_ref.id,
            "remainingCount": new_remaining
        }
    
        if low_stock_warning:
            response["warning"] = low_stock_warning

        return response

    return {
        "success": False,
        "message": "해당 복약 일정을 찾을 수 없습니다.",
        "logId": log_ref.id
    }


# 사용자별 복용 기록 조회 API
@router.get("/user/{user_id}")
def get_user_logs(user_id: str):
    # medicationLogs 컬렉션에서 userId가 일치하는 문서만 조회
    docs = db.collection("medicationLogs").where("userId", "==", user_id).stream()

    logs = []

    for doc in docs:
        log_data = doc.to_dict() or {}
        log_data["logId"] = doc.id
        logs.append(log_data)

    return {
        "success": True,
        "count": len(logs),
        "logs": logs
    }

# =================================
# 이 상비약의 복용 기록도 같이 삭제
# =================================


deleted_log_count = 0


@router.delete(
    "/stock/{user_id}/{log_id}"
)
def delete_stock_log(
    user_id: str,
    log_id: str,
):
    ref = (
        db.collection("logs")
        .document(log_id)
    )

    doc = ref.get()

    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail="복용 기록을 찾을 수 없습니다.",
        )

    log = (
        doc.to_dict()
        or {}
    )

    # 사용자 확인
    if (
        str(
            log.get(
                "userId",
                "",
            )
        )
        != user_id
    ):
        raise HTTPException(
            status_code=403,
            detail="접근 권한이 없습니다.",
        )

    # 상비약 기록만 삭제 허용
    if log.get("isStock") is not True:
        raise HTTPException(
            status_code=400,
            detail=(
                "상비약 복용 기록만 "
                "삭제할 수 있습니다."
            ),
        )

    medicine_name = str(
        log.get(
            "medicineName",
            "",
        )
    )

    ref.delete()

    return {
        "success": True,
        "message":
            "상비약 복용 기록이 삭제되었습니다.",
    
        "deletedLogCount":
            deleted_log_count,
    }