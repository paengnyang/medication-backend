from fastapi import APIRouter, HTTPException, Header
from datetime import datetime, timezone
import uuid
import hashlib
import hmac

from firebase_config import db
from services.login_session_service import (
    create_login_session, get_session_user_id, revoke_login_session
)
from models.schemas import (
    UserCreateRequest,
    UserLoginRequest,
    MedicationTimesUpdateRequest,
    AllergyListUpdateRequest,
)
from google.cloud import firestore
from services.password_service import (
    hash_password,
    verify_user_password,
)
from services.guardian_service import (
    get_linked_guardian_phone,
)
from services.email_verification_service import (
    consume_email_verification,
    get_email_verification_ref,
    get_verification_token_hash,
)


router = APIRouter(
    prefix="/users",
    tags=["Users"]
)


# ============================================================
# 사용자 생성
# ============================================================


@router.post("/create")
def create_user(data: UserCreateRequest):

    email = data.email.strip().lower()
    token = data.verificationToken.strip()

    # ========================================
    # 1. 이메일 중복 확인
    # ========================================

    existing_users = list(
        db.collection("users")
        .where("email", "==", email)
        .limit(1)
        .stream()
    )

    if existing_users:
        raise HTTPException(
            status_code=409,
            detail="이미 가입된 이메일입니다.",
        )

    # ========================================
    # 2. 회원가입 데이터 준비
    # ========================================

    user_id = str(uuid.uuid4())

    password_hash = hash_password(
        data.password
    )

    user_data = {
        "userId": user_id,
        "name": data.name,
        "email": email,
        "phone": data.phone,
        "passwordHash": password_hash,
        "gender": data.gender,
        "pregnancy": data.pregnancy,
        "birthYear": data.birthYear,
        "birthMonth": data.birthMonth,
        "birthDay": data.birthDay,
        "guardianPhone": data.guardianPhone,
        "allergyList": data.allergyList,
        "emailVerified": True,
        "createdAt": datetime.now().isoformat(),
    }

    # ========================================
    # 3. 인증 문서 및 사용자 문서
    # ========================================

    verification_ref = (
        get_email_verification_ref(email)
    )

    user_ref = (
        db.collection("users")
        .document(user_id)
    )

    # 이메일 중복 가입 방지용 문서
    email_key = hashlib.sha256(
        email.encode("utf-8")
    ).hexdigest()

    email_index_ref = (
        db.collection("registered_emails")
        .document(email_key)
    )

    token_hash = get_verification_token_hash(
        token
    )

    # ========================================
    # 4. 트랜잭션 처리
    # ========================================

    transaction = db.transaction()

    @firestore.transactional
    def register_user(transaction):

        # Firestore 트랜잭션:
        # 모든 읽기를 먼저 수행
        verification_doc = verification_ref.get(
            transaction=transaction
        )

        email_index_doc = email_index_ref.get(
            transaction=transaction
        )

        if email_index_doc.exists:
            raise HTTPException(
                status_code=409,
                detail="이미 가입된 이메일입니다.",
            )

        if not verification_doc.exists:
            raise HTTPException(
                status_code=400,
                detail="이메일 인증 정보가 없습니다.",
            )

        verification = (
            verification_doc.to_dict() or {}
        )

        verified_until = verification.get(
            "verifiedUntil"
        )

        if verified_until is None:
            raise HTTPException(
                status_code=400,
                detail="이메일 인증이 완료되지 않았습니다.",
            )

        # Firestore timestamp와 비교
        if verified_until.tzinfo is None:
            verified_until = verified_until.replace(
                tzinfo=timezone.utc
            )

        now = datetime.now(timezone.utc)

        stored_hash = (
            verification.get(
                "verificationTokenHash"
            ) or ""
        )

        valid = (
            verification.get("email") == email
            and verification.get("verified") is True
            and verification.get("used") is not True
            and now < verified_until
            and hmac.compare_digest(
                stored_hash,
                token_hash,
            )
        )

        if not valid:
            raise HTTPException(
                status_code=400,
                detail=(
                    "이메일 인증이 만료되었거나 "
                    "유효하지 않습니다."
                ),
            )

        # ====================================
        # 5. 계정 생성 + 토큰 사용 완료
        # ====================================

        transaction.create(
            user_ref,
            user_data
        )

        transaction.create(
            email_index_ref,
            {
                "email": email,
                "userId": user_id,
                "createdAt": now,
            },
        )

        transaction.update(
            verification_ref,
            {
                "used": True,
                "verificationTokenHash": None,
                "usedAt": now,
            },
        )

    try:
        register_user(transaction)

    except HTTPException:
        raise

    except Exception as e:
        print(
            "[USER CREATE ERROR]",
            type(e).__name__,
            str(e),
        )

        raise HTTPException(
            status_code=500,
            detail="회원가입 처리 중 오류가 발생했습니다.",
        )

    # ========================================
    # 6. 회원가입 성공 응답
    # ========================================

    response_user = user_data.copy()
    response_user.pop("passwordHash", None)

    return {
        "success": True,
        "message": "이메일 인증 및 회원가입 완료",
        "userId": user_id,
        "user": response_user,
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

    session_token = create_login_session(user_data["userId"])

    return {
        "sessionToken": session_token,
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
# 저장된 로그인 세션 확인 / 해제
# 주의: /{user_id}보다 먼저 등록한다.
# ============================================================

@router.get("/session/me")
def get_current_session_user(authorization: str | None = Header(default=None)):
    user_id = get_session_user_id(authorization)
    doc = db.collection("users").document(user_id).get()
    if not doc.exists:
        raise HTTPException(status_code=401, detail="탈퇴하거나 삭제된 계정입니다.")
    user = doc.to_dict() or {}
    user.pop("passwordHash", None)
    user["userId"] = user_id
    linked_phone = get_linked_guardian_phone(user_id)
    if linked_phone:
        user["guardianPhone"] = linked_phone
    return {"success": True, "user": user}


@router.post("/session/logout")
def logout_session(authorization: str | None = Header(default=None)):
    get_session_user_id(authorization)
    revoke_login_session(authorization)
    return {"success": True, "message": "로그아웃 완료"}


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

# ============================================================
# 사용자 알레르기 목록 수정
# ============================================================

@router.patch(
    "/{user_id}/allergies"
)
async def update_allergies(
    user_id: str,
    request:
        AllergyListUpdateRequest,
):
    try:
        user_ref = (
            db.collection("users")
            .document(user_id)
        )

        user_doc = (
            user_ref.get()
        )

        if not user_doc.exists:
            return {
                "success": False,
                "message":
                    "사용자를 찾을 수 없습니다.",
            }

        # --------------------------------
        # 공백 제거 + 빈 문자열 제거
        # --------------------------------

        cleaned = []

        for allergy in (
            request.allergyList
        ):
            value = (
                str(allergy)
                .strip()
            )

            if not value:
                continue

            if value not in cleaned:
                cleaned.append(
                    value
                )

        user_ref.update({
            "allergyList":
                cleaned,
        })

        return {
            "success": True,
            "userId":
                user_id,
            "allergyList":
                cleaned,
            "message":
                "알레르기 정보가 저장되었습니다.",
        }

    except Exception as e:
        print(
            "알레르기 목록 저장 오류:",
            str(e),
        )

        return {
            "success": False,
            "message":
                "알레르기 정보 저장 중 "
                "오류가 발생했습니다.",
            "error":
                str(e),
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