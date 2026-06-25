from fastapi import APIRouter
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

        return {
            "success": True,
            "message": "복용 기록 저장 완료",
            "logId": log_ref.id,
            "remainingCount": new_remaining
        }

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
        log = doc.to_dict()
        log["logId"] = doc.id
        logs.append(log)

    return {
        "success": True,
        "count": len(logs),
        "logs": logs
    }