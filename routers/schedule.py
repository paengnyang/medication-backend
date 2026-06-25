from fastapi import APIRouter
from datetime import datetime

from firebase_config import db
from models.schemas import ScheduleCreateRequest
from services.schedule_service import generate_times


# schedule 관련 API를 모아두는 라우터
router = APIRouter(
    prefix="/schedules",
    tags=["Schedules"]
)


# 복약 일정 생성 API
@router.post("/create")
def create_schedule(data: ScheduleCreateRequest):
    # 하루 복용 횟수에 따라 기본 복약 시간 생성
    times = generate_times(data.dailyCount)

    # 전체 약 개수 계산
    # 예: 하루 3회, 7일분, 1회 1정 = 총 21정
    total_count = data.dailyCount * data.period * data.dosage

    # Firestore schedules 컬렉션에 새 문서 생성
    doc_ref = db.collection("schedules").document()

    schedule_data = {
        "userId": data.userId,
        "medicineName": data.medicineName,
        "dailyCount": data.dailyCount,
        "times": times,
        "dosage": data.dosage,
        "timing": data.timing,
        "startDate": data.startDate,
        "endDate": data.endDate,
        "period": data.period,
        "totalCount": total_count,
        "remainingCount": total_count,
        "active": True,
        "createdAt": datetime.now().isoformat()
    }

    # Firestore에 저장
    doc_ref.set(schedule_data)

    return {
        "success": True,
        "message": "복약 일정 생성 완료",
        "scheduleId": doc_ref.id,
        "schedule": schedule_data
    }


# 사용자별 복약 일정 조회 API
@router.get("/user/{user_id}")
def get_user_schedules(user_id: str):
    # schedules 컬렉션에서 userId가 일치하는 문서만 조회
    docs = db.collection("schedules").where("userId", "==", user_id).stream()

    schedules = []

    for doc in docs:
        schedule = doc.to_dict()
        schedule["scheduleId"] = doc.id
        schedules.append(schedule)

    return {
        "success": True,
        "count": len(schedules),
        "schedules": schedules
    }