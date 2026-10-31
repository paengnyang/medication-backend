import hashlib
import secrets

from datetime import (
    datetime,
    timedelta,
    timezone,
    date,
)

from firebase_config import db

GUARDIAN_CODE_EXPIRE_MINUTES = 10


# ============================================================
# 현재 시간
# ============================================================

def _now():
    return datetime.now(
        timezone.utc
    )


# ============================================================
# 연결 코드 해시
# ============================================================

def _hash_code(
    code: str
) -> str:

    return hashlib.sha256(
        code.encode("utf-8")
    ).hexdigest()


# ============================================================
# 사용자 조회
# ============================================================

def _get_user(
    user_id: str
):

    user_id = str(
        user_id
    ).strip()

    if not user_id:
        return None

    doc = (
        db.collection("users")
        .document(user_id)
        .get()
    )

    if not doc.exists:
        return None

    data = doc.to_dict() or {}

    return {
        "userId": doc.id,
        "name": data.get("name"),
        "email": data.get("email"),
        "phone": data.get("phone"),
    }


# ============================================================
# 보호자 연결 코드 생성
# ============================================================

def create_guardian_code(
    ward_user_id: str
):

    ward_user_id = str(
        ward_user_id
    ).strip()

    ward = _get_user(
        ward_user_id
    )

    if ward is None:
        raise ValueError(
            "사용자 정보를 찾을 수 없습니다."
        )

    # 100000 ~ 999999
    code = str(
        secrets.randbelow(
            900000
        )
        + 100000
    )

    now = _now()

    expires_at = (
        now
        + timedelta(
            minutes=
                GUARDIAN_CODE_EXPIRE_MINUTES
        )
    )

    doc_ref = (
        db.collection(
            "guardian_link_codes"
        )
        .document()
    )

    doc_ref.set({
        "wardUserId":
            ward_user_id,

        # 실제 코드는 Firestore에 평문 저장하지 않음
        "codeHash":
            _hash_code(code),

        "createdAt":
            now,

        "expiresAt":
            expires_at,

        "used":
            False,
    })

    return {
        "wardUserId":
            ward_user_id,

        "wardName":
            ward.get("name"),

        "connectionCode":
            code,

        "expiresInMinutes":
            GUARDIAN_CODE_EXPIRE_MINUTES,
    }


# ============================================================
# 연결 코드 조회
# ============================================================

def _find_connection_code(
    code: str
):

    code = str(
        code
    ).strip()

    code_hash = _hash_code(
        code
    )

    docs = (
        db.collection(
            "guardian_link_codes"
        )
        .where(
            "codeHash",
            "==",
            code_hash,
        )
        .stream()
    )

    for doc in docs:

        data = (
            doc.to_dict()
            or {}
        )

        # 이미 사용한 코드
        if data.get(
            "used",
            False,
        ):
            continue

        expires_at = (
            data.get(
                "expiresAt"
            )
        )

        # 만료된 코드
        if (
            expires_at is None
            or expires_at < _now()
        ):
            continue

        return {
            "documentId":
                doc.id,

            "data":
                data,
        }

    return None


# ============================================================
# 기존 연결 확인
# ============================================================

def _find_existing_link(
    ward_user_id: str,
    guardian_user_id: str,
):

    docs = (
        db.collection(
            "guardian_links"
        )
        .where(
            "wardUserId",
            "==",
            ward_user_id,
        )
        .stream()
    )

    for doc in docs:

        data = (
            doc.to_dict()
            or {}
        )

        if (
            data.get(
                "guardianUserId"
            )
            == guardian_user_id
            and
            data.get(
                "status"
            )
            == "active"
        ):
            return {
                "linkId":
                    doc.id,

                **data,
            }

    return None


# ============================================================
# 보호자 연결
# ============================================================

def connect_guardian(
    guardian_user_id: str,
    connection_code: str,
):

    guardian_user_id = str(
        guardian_user_id
    ).strip()

    connection_code = str(
        connection_code
    ).strip()

    guardian = _get_user(
        guardian_user_id
    )

    if guardian is None:
        raise ValueError(
            "보호자 계정을 찾을 수 없습니다."
        )

    code_result = (
        _find_connection_code(
            connection_code
        )
    )

    if code_result is None:
        raise ValueError(
            "연결 코드가 올바르지 않거나 만료되었습니다."
        )

    code_data = (
        code_result["data"]
    )

    ward_user_id = (
        code_data.get(
            "wardUserId"
        )
    )

    if not ward_user_id:
        raise ValueError(
            "연결 대상 사용자 정보가 없습니다."
        )

    # 자기 자신 연결 방지
    if (
        guardian_user_id
        == ward_user_id
    ):
        raise ValueError(
            "자기 자신을 보호자로 연결할 수 없습니다."
        )

    ward = _get_user(
        ward_user_id
    )

    if ward is None:
        raise ValueError(
            "연결 대상 사용자를 찾을 수 없습니다."
        )

    existing_link = (
        _find_existing_link(
            ward_user_id,
            guardian_user_id,
        )
    )

    if existing_link:
        raise ValueError(
            "이미 연결된 보호자입니다."
        )

    # ========================================
    # 실제 연결 정보 저장
    # ========================================

    link_ref = (
        db.collection(
            "guardian_links"
        )
        .document()
    )

    link_data = {
        "wardUserId":
            ward_user_id,

        "guardianUserId":
            guardian_user_id,

        "status":
            "active",

        "createdAt":
            _now(),
    }

    link_ref.set(
        link_data
    )

    # 연결 코드 사용 완료 처리
    code_ref = (
        db.collection(
            "guardian_link_codes"
        )
        .document(
            code_result[
                "documentId"
            ]
        )
    )

    code_ref.update({
        "used":
            True,

        "usedAt":
            _now(),

        "guardianUserId":
            guardian_user_id,
    })

    return {
        "linkId":
            link_ref.id,

        "ward": {
            "userId":
                ward_user_id,

            "name":
                ward.get("name"),
        },

        "guardian": {
            "userId":
                guardian_user_id,

            "name":
                guardian.get("name"),
        },

        "status":
            "active",
    }


# ============================================================
# 복약 사용자의 보호자 목록
# ============================================================

 
def get_guardians_for_ward(ward_user_id: str):
    ward_user_id = str(ward_user_id).strip()
    docs = (
        db.collection("guardian_links")
        .where("wardUserId", "==", ward_user_id)
        .stream()
    )
    result = []
    for doc in docs:
        data = doc.to_dict() or {}
        if data.get("status") != "active":
            continue
        guardian_user_id = data.get("guardianUserId")
        guardian = _get_user(guardian_user_id)
        result.append({
            "linkId": doc.id,
            "guardianUserId": guardian_user_id,
            "guardianName": guardian.get("name") if guardian else None,
            "guardianPhone": guardian.get("phone") if guardian else None,
            "status": "active",
        })
    return result


# ============================================================
# 보호자가 관리하는 사용자 목록
# ============================================================

def get_wards_for_guardian(guardian_user_id: str):
    guardian_user_id = str(guardian_user_id).strip()
    docs = (
        db.collection("guardian_links")
        .where("guardianUserId", "==", guardian_user_id)
        .stream()
    )
    result = []
    for doc in docs:
        data = doc.to_dict() or {}
        if data.get("status") != "active":
            continue
        ward_user_id = data.get("wardUserId")
        ward = _get_user(ward_user_id)
        result.append({
            "linkId": doc.id,
            "wardUserId": ward_user_id,
            "wardName": ward.get("name") if ward else None,
            "status": "active",
        })
    return result


# ============================================================
# 보호자-사용자 연결 확인
# ============================================================

def _check_active_guardian_link(
    guardian_user_id: str,
    ward_user_id: str,
):

    docs = (
        db.collection("guardian_links")
        .where(
            "guardianUserId",
            "==",
            guardian_user_id,
        )
        .stream()
    )

    for doc in docs:

        data = (
            doc.to_dict()
            or {}
        )

        if (
            data.get("wardUserId")
            == ward_user_id
            and
            data.get("status")
            == "active"
        ):
            return {
                "linkId": doc.id,
                **data,
            }

    return None


# ============================================================
# 보호자 연결 해제
# ============================================================

def disconnect_guardian(
    ward_user_id: str,
    guardian_user_id: str,
):

    ward_user_id = str(
        ward_user_id
    ).strip()

    guardian_user_id = str(
        guardian_user_id
    ).strip()

    if not ward_user_id or not guardian_user_id:
        raise ValueError(
            "사용자 ID가 올바르지 않습니다."
        )

    # ========================================
    # 실제 연결 정보 조회
    # ========================================

    docs = (
        db.collection(
            "guardian_links"
        )
        .where(
            "wardUserId",
            "==",
            ward_user_id,
        )
        .stream()
    )

    target_links = []

    for doc in docs:

        data = doc.to_dict() or {}

        if (
            str(data.get("guardianUserId", "")).strip()
            != guardian_user_id
        ):
            continue

        if data.get("status") != "active":
            continue

        target_links.append(doc)
   
    if not target_links:
        raise ValueError(
            "연결된 보호자를 찾을 수 없습니다."
        )

    # ========================================
    # 연결 상태 변경
    # ========================================

    for doc in target_links:

        doc.reference.update({
            "status": "disconnected",
            "disconnectedAt": _now(),
        })

    return {
        "wardUserId": ward_user_id,
        "guardianUserId": guardian_user_id,
        "disconnectedCount": len(target_links),
        "status": "disconnected",
    }


# ============================================================
# 날짜가 복약 일정 안에 포함되는지 확인
# ============================================================

def _schedule_is_for_date(
    schedule: dict,
    target_date: str,
):

    if schedule.get(
        "active",
        True,
    ) is False:
        return False

    start_date = str(
        schedule.get(
            "startDate",
            ""
        )
    ).strip()

    end_date = str(
        schedule.get(
            "endDate",
            ""
        )
    ).strip()

    try:
        target = date.fromisoformat(
            target_date
        )
    except ValueError:
        return False

    if start_date:
        try:

            start = date.fromisoformat(
                start_date[:10]
            )

            if target < start:
                return False

        except ValueError:
            pass

    if end_date:
        try:

            end = date.fromisoformat(
                end_date[:10]
            )

            if target > end:
                return False

        except ValueError:
            pass

    return True


# ============================================================
# dailyCount 기준 기본 복약 시간
# ============================================================

def _default_times(
    daily_count: int
):

    if daily_count <= 1:
        return [
            "아침"
        ]

    if daily_count == 2:
        return [
            "아침",
            "저녁",
        ]

    if daily_count == 3:
        return [
            "아침",
            "점심",
            "저녁",
        ]

    return [
        f"{i + 1}회"
        for i in range(
            daily_count
        )
    ]


# ============================================================
# 보호자가 사용자의 오늘 복약 현황 조회
# ============================================================

def get_ward_today_medication_status(
    guardian_user_id: str,
    ward_user_id: str,
):

    guardian_user_id = str(
        guardian_user_id
    ).strip()

    ward_user_id = str(
        ward_user_id
    ).strip()

    # --------------------------------
    # 1. 보호자-사용자 연결 확인
    # --------------------------------

    link = (
        _check_active_guardian_link(
            guardian_user_id,
            ward_user_id,
        )
    )

    if link is None:
        raise ValueError(
            "해당 사용자를 조회할 권한이 없습니다."
        )

    # --------------------------------
    # 2. 사용자 정보
    # --------------------------------

    ward = (
        _get_user(
            ward_user_id
        )
    )

    if ward is None:
        raise ValueError(
            "사용자 정보를 찾을 수 없습니다."
        )

    # --------------------------------
    # 3. 한국 날짜 기준 오늘
    # --------------------------------
    KST = timezone(
    timedelta(hours=9)
    )

    korea_now = datetime.now(
    KST
    )
    

    today = (
        korea_now
        .date()
        .isoformat()
    )

    # --------------------------------
    # 4. 복약 일정 조회
    # --------------------------------

    schedule_docs = (
        db.collection(
            "schedules"
        )
        .where(
            "userId",
            "==",
            ward_user_id,
        )
        .stream()
    )

    schedules = []

    for doc in schedule_docs:

        data = (
            doc.to_dict()
            or {}
        )

        if not _schedule_is_for_date(
            data,
            today,
        ):
            continue

        data[
            "scheduleId"
        ] = doc.id

        schedules.append(
            data
        )

    # --------------------------------
    # 5. 오늘 복약 로그 조회
    # --------------------------------

    log_docs = (
        db.collection("medicationLogs")
        .stream()
    )

    today_logs = []

    for doc in log_docs:

        data = (
            doc.to_dict()
            or {}
        )

        log_user_id = str(
            data.get(
                "userId",
                ""
            )
        ).strip()

        log_date = str(
            data.get(
                "date",
                ""
            )
        ).strip()

        log_taken = data.get(
            "taken",
            False,
        )

        # 다른 사용자 로그 제외
        if log_user_id != ward_user_id:
            continue

        # 오늘 기록만
        if log_date != today:
            continue

        # 실제 복용 완료 기록만
        if log_taken is not True:
            continue

        data["logId"] = doc.id

        today_logs.append(
            data
        )

    
    log_by_schedule_time = {}

    # 예전 로그에 scheduleId가 없는 경우를 위한
    # 약 이름 기반 보조 인덱스
    log_by_medicine_time = {}

    for log in today_logs:

        log_schedule_id = str(
            log.get(
                "scheduleId",
                ""
            )
        ).strip()

        log_medicine_name = str(
            log.get(
                "medicineName",
                ""
            )
        ).strip()

        log_time = str(
            log.get(
                "time",
                ""
            )
        ).strip()

        if (
            log_schedule_id
            and log_time
        ):
            log_by_schedule_time[
                (
                    log_schedule_id,
                    log_time,
                )
            ] = log

        if (
            log_medicine_name
            and log_time
        ):
            log_by_medicine_time[
                (
                    log_medicine_name,
                    log_time,
                )
            ] = log

    # --------------------------------
    # 6. 일정 + 복약 로그 결합
    # --------------------------------

    medications = []

    total_doses = 0
    taken_doses = 0

    for schedule in schedules:

        schedule_id = str(
            schedule.get(
                "scheduleId",
                ""
            )
        ).strip()

        medicine_name = str(
            schedule.get(
                "medicineName",
                ""
            )
        ).strip()

        try:
            daily_count = int(
                schedule.get(
                    "dailyCount",
                    1
                )
            )

        except (
            ValueError,
            TypeError,
        ):
            daily_count = 1

        schedule_times = (
            schedule.get(
                "times"
            )
        )

        if (
            not isinstance(
                schedule_times,
                list,
            )
            or not schedule_times
        ):
            schedule_times = (
                _default_times(
                    daily_count
                )
            )

        medication_taken_count = 0

        dose_status = []

        for scheduled_time in schedule_times:

            scheduled_time = str(
                scheduled_time
            ).strip()

            # ========================================
            # 1순위:
            # scheduleId + 시간으로 정확히 찾기
            # ========================================

            taken_log = (
                log_by_schedule_time.get(
                    (
                        schedule_id,
                        scheduled_time,
                    )
                )
            )

            # ========================================
            # 2순위:
            # 이전 데이터 호환
            # medicineName + 시간으로 찾기
            # ========================================

            if taken_log is None:

                taken_log = (
                    log_by_medicine_time.get(
                        (
                            medicine_name,
                            scheduled_time,
                        )
                    )
                )

            taken = (
                taken_log
                is not None
            )

            if taken:
                medication_taken_count += 1
                taken_doses += 1

            total_doses += 1

            dose_status.append({
                "time":
                    scheduled_time,

                "taken":
                    taken,

                "logId":
                    (
                        taken_log.get(
                            "logId"
                        )
                        if taken_log
                        else None
                    ),

                "takenAt":
                    (
                        taken_log.get(
                            "takenAt"
                        )
                        if taken_log
                        else None
                    ),
            })

        medications.append({
            "scheduleId":
                schedule_id,

            "medicineName":
                medicine_name,

            "dailyCount":
                daily_count,

            "dosage":
                schedule.get(
                    "dosage"
                ),

            "timing":
                schedule.get(
                    "timing"
                ),

            "remainingCount":
                schedule.get(
                    "remainingCount"
                ),

            "expectedDoseCount":
                len(
                    schedule_times
                ),

            "takenDoseCount":
                medication_taken_count,

            "completed":
                (
                    medication_taken_count
                    >= len(
                        schedule_times
                    )
                ),

            "doses":
                dose_status,
            "prescriptionGroupId": schedule.get(
                "prescriptionGroupId", ""
            ),
            "prescriptionCategory": schedule.get(
                    "prescriptionCategory", ""
            ),
        })

    remaining_doses = max(
        total_doses
        - taken_doses,
        0,
    )

    # --------------------------------
    # 7. 응답
    # --------------------------------

    return {
        "guardianUserId":
            guardian_user_id,

       

        "ward": {
            "userId":
                ward_user_id,

            "name":
                ward.get(
                    "name"
                ),
        },

        "date":
            today,

        "summary": {
            "total":
                total_doses,

            "taken":
                taken_doses,

            "remaining":
                remaining_doses,

            "completionRate":
                (
                    round(
                        (
                            taken_doses
                            / total_doses
                        )
                        * 100,
                        1,
                    )
                    if total_doses > 0
                    else 0
                ),
        },

        "medications":
            medications,
    }

def get_linked_guardian_phone(
    ward_user_id: str
):
    ward_user_id = str(
        ward_user_id
    ).strip()

    if not ward_user_id:
        return None

    link_docs = (
        db.collection(
            "guardian_links"
        )
        .where(
            "wardUserId",
            "==",
            ward_user_id,
        )
        .stream()
    )

    for link_doc in link_docs:

        link_data = (
            link_doc.to_dict()
            or {}
        )

        # 활성화된 연결만 사용
        if (
            link_data.get(
                "status"
            )
            != "active"
        ):
            continue

        guardian_user_id = (
            link_data.get(
                "guardianUserId"
            )
        )

        if not guardian_user_id:
            continue

        guardian_doc = (
            db.collection(
                "users"
            )
            .document(
                guardian_user_id
            )
            .get()
        )

        if not guardian_doc.exists:
            continue

        guardian_data = (
            guardian_doc.to_dict()
            or {}
        )

        phone = str(
            guardian_data.get(
                "phone",
                ""
            )
        ).strip()

        if phone:
            return phone

    return None

def get_ward_medication_history_by_date(
    guardian_user_id: str,
    ward_user_id: str,
    target_date: str,
):
    guardian_user_id = str(
        guardian_user_id
    ).strip()

    ward_user_id = str(
        ward_user_id
    ).strip()

    target_date = str(
        target_date
    ).strip()

    # --------------------------------
    # 1. 날짜 형식 검사
    # --------------------------------

    try:
        datetime.strptime(
            target_date,
            "%Y-%m-%d",
        )

    except ValueError:
        raise ValueError(
            "날짜는 YYYY-MM-DD 형식이어야 합니다."
        )

    # --------------------------------
    # 2. 보호자-사용자 연결 확인
    # --------------------------------

    if _check_active_guardian_link(
        guardian_user_id,
        ward_user_id,
    ) is None:
        raise ValueError("해당 사용자의 복약 기록을 조회할 권한이 없습니다.")

    # --------------------------------
    # 3. 사용자 정보 조회
    # --------------------------------

    ward_doc = (
        db.collection("users")
        .document(ward_user_id)
        .get()
    )

    if not ward_doc.exists:
        raise ValueError(
            "사용자를 찾을 수 없습니다."
        )

    ward_data = (
        ward_doc.to_dict()
        or {}
    )

    # --------------------------------
    # 4. 해당 날짜에 유효했던 복약 일정 조회
    #
    # 주의:
    # 과거 기록 조회이므로 현재 active=False여도
    # 당시 복약 기간에 포함되면 조회해야 함
    # --------------------------------

    schedule_docs = (
        db.collection("schedules")
        .where(
            "userId",
            "==",
            ward_user_id,
        )
        .stream()
    )

    schedules = []

    for doc in schedule_docs:

        data = (
            doc.to_dict()
            or {}
        )

        start_date = str(
            data.get(
                "startDate",
                ""
            )
        ).strip()

        end_date = str(
            data.get(
                "endDate",
                ""
            )
        ).strip()

        # 복약 시작 전이면 제외
        if (
            start_date
            and target_date < start_date
        ):
            continue

        # 복약 종료 후면 제외
        if (
            end_date
            and target_date > end_date
        ):
            continue

        data["scheduleId"] = doc.id

        schedules.append(
            data
        )

    # --------------------------------
    # 5. 해당 날짜의 복용 로그 조회
    # --------------------------------

    log_docs = (
        db.collection(
            "medicationLogs"
        )
        .where(
            "userId",
            "==",
            ward_user_id,
        )
        .stream()
    )

    target_logs = []

    for doc in log_docs:

        data = (
            doc.to_dict()
            or {}
        )

        log_date = str(
            data.get(
                "date",
                ""
            )
        ).strip()

        if log_date != target_date:
            continue

        if data.get(
            "taken",
            False,
        ) is not True:
            continue

        data["logId"] = doc.id

        target_logs.append(
            data
        )

    # --------------------------------
    # 6. 로그 인덱스 생성
    # --------------------------------

    log_by_schedule_time = {}

    log_by_medicine_time = {}

    for log in target_logs:

        log_schedule_id = str(
            log.get(
                "scheduleId",
                ""
            )
        ).strip()

        log_medicine_name = str(
            log.get(
                "medicineName",
                ""
            )
        ).strip()

        log_time = str(
            log.get(
                "time",
                ""
            )
        ).strip()

        # 가장 정확한 방식
        if (
            log_schedule_id
            and log_time
        ):
            log_by_schedule_time[
                (
                    log_schedule_id,
                    log_time,
                )
            ] = log

        # 옛날 로그 중 scheduleId가 없는 경우 대비
        if (
            not log_schedule_id
            and log_medicine_name
            and log_time
        ):
            log_by_medicine_time[
                (
                    log_medicine_name,
                    log_time,
                )
            ] = log

    # --------------------------------
    # 7. 일정과 복용 기록 결합
    # --------------------------------

    medications = []

    total_doses = 0
    taken_doses = 0

    for schedule in schedules:

        schedule_id = str(
            schedule.get(
                "scheduleId",
                ""
            )
        ).strip()

        medicine_name = str(
            schedule.get(
                "medicineName",
                ""
            )
        ).strip()

        try:
            daily_count = int(
                schedule.get(
                    "dailyCount",
                    1,
                )
            )

        except (
            ValueError,
            TypeError,
        ):
            daily_count = 1

        schedule_times = (
            schedule.get(
                "times"
            )
        )

        if (
            not isinstance(
                schedule_times,
                list,
            )
            or not schedule_times
        ):
            schedule_times = (
                _default_times(
                    daily_count
                )
            )

        medication_taken_count = 0

        doses = []

        for scheduled_time in schedule_times:

            scheduled_time = str(
                scheduled_time
            ).strip()

            # scheduleId + time으로 우선 조회
            taken_log = (
                log_by_schedule_time.get(
                    (
                        schedule_id,
                        scheduled_time,
                    )
                )
            )

            # 옛 데이터 호환용
            if taken_log is None:

                taken_log = (
                    log_by_medicine_time.get(
                        (
                            medicine_name,
                            scheduled_time,
                        )
                    )
                )

            taken = (
                taken_log
                is not None
            )

            if taken:
                medication_taken_count += 1
                taken_doses += 1

            total_doses += 1

            doses.append({
                "time":
                    scheduled_time,

                "taken":
                    taken,

                "logId":
                    (
                        taken_log.get(
                            "logId"
                        )
                        if taken_log
                        else None
                    ),

                "takenAt":
                    (
                        taken_log.get(
                            "takenAt"
                        )
                        if taken_log
                        else None
                    ),
            })

        medications.append({
            "scheduleId":
                schedule_id,

            "medicineName":
                medicine_name,

            "dailyCount":
                daily_count,

            "dosage":
                schedule.get(
                    "dosage"
                ),

            "timing":
                schedule.get(
                    "timing"
                ),

            # 이 값은 현재 시점의 남은 수량임
            "currentRemainingCount":
                schedule.get(
                    "remainingCount"
                ),

            "expectedDoseCount":
                len(
                    schedule_times
                ),

            "takenDoseCount":
                medication_taken_count,

            "completed":
                (
                    medication_taken_count
                    >= len(schedule_times)
                ),

            "doses":
                doses,
            "prescriptionGroupId": schedule.get(
                "prescriptionGroupId", ""
            ),
            "prescriptionCategory": schedule.get(
                "prescriptionCategory", ""
            ),
        })

    # --------------------------------
    # 8. 요약
    # --------------------------------

    remaining_doses = max(
        total_doses - taken_doses,
        0,
    )

    completion_rate = (
        round(
            (
                taken_doses
                / total_doses
            )
            * 100,
            1,
        )
        if total_doses > 0
        else 0
    )

    # --------------------------------
    # 9. 결과
    # --------------------------------

    return {
        "success":
            True,

        "guardianUserId":
            guardian_user_id,

        "ward": {
            "userId":
                ward_user_id,

            "name":
                ward_data.get(
                    "name"
                ),
        },

        "date":
            target_date,

        "summary": {
            "total":
                total_doses,

            "taken":
                taken_doses,

            "remaining":
                remaining_doses,

            "completionRate":
                completion_rate,
        },

        "medications":
            medications,
    }