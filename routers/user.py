from fastapi import APIRouter, HTTPException
from datetime import datetime
import uuid

from firebase_config import db
from models.schemas import (
    UserCreateRequest,
    UserLoginRequest,
    MedicationTimesUpdateRequest,
)

from services.password_service import (
    hash_password,
    verify_user_password,
)
from services.guardian_service import (
    get_linked_guardian_phone,
)


router = APIRouter(
    prefix="/users",
    tags=["Users"]
)


# ============================================================
# 사용자 생성
# ============================================================

@router.post("/create")
def create_user(
    data: UserCreateRequest
):

    existing_users = list(
        db.collection("users")
        .where(
            "email",
            "==",
            data.email
        )
        .limit(1)
        .stream()
    )

    if existing_users:
        raise HTTPException(
            status_code=409,
            detail="이미 가입된 이메일입니다."
        )

    user_id = str(
        uuid.uuid4()
    )

    # ✅ 비밀번호 암호화
    password_hash = (
        hash_password(
            data.password
        )
    )

    user_data = {
        "userId":
            user_id,

        "name":
            data.name,

        "email":
            data.email,

        "phone":
            data.phone,

        "passwordHash":
            password_hash,

        "gender":
            data.gender,

        "pregnancy":
            data.pregnancy,

        "birthYear":
            data.birthYear,

        "birthMonth":
            data.birthMonth,

        "birthDay":
            data.birthDay,

        "guardianPhone":
            data.guardianPhone,

        "allergyList":
            data.allergyList,

        "createdAt":
            datetime.now().isoformat()
    }

    db.collection(
        "users"
    ).document(
        user_id
    ).set(
        user_data
    )

    response_user = (
        user_data.copy()
    )

    response_user.pop(
        "passwordHash",
        None
    )

    return {
        "success":
            True,

        "message":
            "사용자 정보 저장 완료",

        "userId":
            user_id,

        "user":
            response_user
    }


# ============================================================
# 로그인
# ============================================================

@router.post("/login")
def login_user(
    data: UserLoginRequest
):

    users = list(
        db.collection("users")
        .where(
            "email",
            "==",
            data.email
        )
        .limit(1)
        .stream()
    )

    if not users:
        raise HTTPException(
            status_code=401,
            detail=
                "이메일 또는 비밀번호가 올바르지 않습니다."
        )

    user_doc = users[0]

    user_data = (
        user_doc.to_dict()
    )

    # ✅ password_service 사용
    if not verify_user_password(
        user_data,
        data.password,
    ):
        raise HTTPException(
            status_code=401,
            detail=
                "이메일 또는 비밀번호가 올바르지 않습니다."
        )

    user_data.pop(
        "passwordHash",
        None
    )

    return {
        "success":
            True,

        "message":
            "로그인 성공",

        "userId":
            user_data["userId"],

        "user":
            user_data
    }


# ============================================================
# 사용자 정보 조회
# ============================================================

@router.get("/{user_id}")
def get_user(
    user_id: str
):

    user_ref = (
        db.collection(
            "users"
        ).document(
            user_id
        )
    )

    user_doc = (
        user_ref.get()
    )

    if not user_doc.exists:
        return {
            "success":
                False,

            "message":
                "사용자를 찾을 수 없습니다."
        }

    user_data = (
        user_doc.to_dict()
    )

    user_data.pop(
        "passwordHash",
        None
    )
    linked_guardian_phone = (
        get_linked_guardian_phone(
            user_id
        )
    )

    if linked_guardian_phone:
        user_data[
            "guardianPhone"
        ] = linked_guardian_phone
    return {
        "success":
            True,

        "user":
            user_data
    }
@router.patch("/{user_id}/medication-times")
def update_medication_times(
    user_id: str,
    data: MedicationTimesUpdateRequest,
):
    user_ref = (
        db.collection("users")
        .document(user_id)
    )

    user_doc = user_ref.get()

    if not user_doc.exists:
        raise HTTPException(
            status_code=404,
            detail="사용자를 찾을 수 없습니다.",
        )

    # ===============================
    # HH:mm 형식 확인
    # ===============================

    def validate_time(value: str):
        try:
            datetime.strptime(
                value,
                "%H:%M",
            )
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=(
                    "복약 시간은 HH:mm "
                    "형식이어야 합니다."
                ),
            )

        return value

    morning = validate_time(
        data.morning
    )

    lunch = validate_time(
        data.lunch
    )

    dinner = validate_time(
        data.dinner
    )

    medication_times = {
        "morning": morning,
        "lunch": lunch,
        "dinner": dinner,
    }

    # ===============================
    # 사용자 설정 저장
    # ===============================

    user_ref.update({
        "medicationTimes":
            medication_times,

        "updatedAt":
            datetime.now()
            .isoformat(),
    })

    # ===============================
    # 현재 등록된 복약 일정도 변경
    # ===============================

    schedule_docs = (
        db.collection("schedules")
        .where(
            "userId",
            "==",
            user_id,
        )
        .stream()
    )

    updated_schedule_count = 0

    today = datetime.now().strftime(
        "%Y-%m-%d"
    )

    for schedule_doc in schedule_docs:
        schedule = (
            schedule_doc.to_dict()
        )

        # 상비약 제외
        if (
            schedule.get("isStock")
            is True
        ):
            continue

        if (
            schedule.get("active")
            is False
        ):
            continue

        try:
            daily_count = int(
                schedule.get(
                    "dailyCount",
                    0,
                )
                or 0
            )
        except (
            ValueError,
            TypeError,
        ):
            continue

        old_times = [
            str(value)
            for value
            in (
                schedule.get(
                    "times",
                    [],
                )
                or []
            )
        ]

        if daily_count == 1:
            new_times = [
                morning,
            ]

        elif daily_count == 2:
            new_times = [
                morning,
                dinner,
            ]

        elif daily_count == 3:
            new_times = [
                morning,
                lunch,
                dinner,
            ]

        else:
            # 4회 이상은
            # 아침/점심/저녁만으로
            # 임의 변경하지 않음
            continue

        schedule_doc.reference.update({
            "times":
                new_times,

            "updatedAt":
                datetime.now()
                .isoformat(),
        })

        updated_schedule_count += 1

        # =================================
        # 오늘 이미 복용한 로그가 있으면
        # 바뀐 시간에 맞춰 유지
        # =================================

        if (
            old_times
            and len(old_times)
                == len(new_times)
        ):
            time_map = {
                old_times[i]:
                    new_times[i]
                for i
                in range(
                    len(old_times)
                )
            }

            log_docs = (
                db.collection("logs")
                .where(
                    "scheduleId",
                    "==",
                    schedule_doc.id,
                )
                .stream()
            )

            for log_doc in log_docs:
                log = (
                    log_doc.to_dict()
                )

                if (
                    log.get("userId")
                    != user_id
                ):
                    continue

                if (
                    str(
                        log.get(
                            "date",
                            "",
                        )
                    )
                    != today
                ):
                    continue

                old_log_time = str(
                    log.get(
                        "time",
                        "",
                    )
                )

                new_log_time = (
                    time_map.get(
                        old_log_time
                    )
                )

                if (
                    new_log_time
                    and
                    new_log_time
                    != old_log_time
                ):
                    log_doc.reference.update({
                        "time":
                            new_log_time,
                    })

    return {
        "success": True,
        "message":
            "복약 시간 설정 완료",

        "medicationTimes":
            medication_times,

        "updatedScheduleCount":
            updated_schedule_count,
    }