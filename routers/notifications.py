
import hashlib
import hmac
import os
from datetime import datetime, date, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import Optional

from fastapi import APIRouter, HTTPException, Header, Query
from pydantic import BaseModel, Field
from google.api_core.exceptions import AlreadyExists
from firebase_admin import messaging
from google.cloud import firestore

from firebase_config import db


router = APIRouter(
    prefix="/notifications",
    tags=["Notifications"],
)

KST = ZoneInfo("Asia/Seoul")
COLLECTION = "notifications"
RETENTION_DAYS = 14


# ============================================
# 공통 함수
# ============================================

def now_kst():
    return datetime.now(KST)


def _hash(*parts):
    text = "|".join(map(str, parts))
    return hashlib.sha256(
        text.encode("utf-8")
    ).hexdigest()


def _dt(value):
    if isinstance(value, datetime):
        if value.tzinfo:
            return value

        return value.replace(
            tzinfo=timezone.utc
        )

    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value.replace("Z", "+00:00")
            )

            if parsed.tzinfo:
                return parsed

            return parsed.replace(
                tzinfo=timezone.utc
            )
        except ValueError:
            return None

    return None


def _day(value):
    try:
        return date.fromisoformat(
            str(value)[:10]
        )
    except (ValueError, TypeError):
        return None


def _time(value):
    try:
        return datetime.strptime(
            str(value).strip(),
            "%H:%M",
        ).strftime("%H:%M")
    except (ValueError, TypeError):
        return None


def _meal(value):
    hour = int(value[:2])

    if hour < 11:
        return "아침"

    if hour < 17:
        return "점심"

    return "저녁"


def _user(user_id):
    snap = (
        db.collection("users")
        .document(user_id)
        .get()
    )

    if not snap.exists:
        raise HTTPException(
            status_code=404,
            detail="사용자를 찾을 수 없습니다.",
        )

    return snap.to_dict() or {}


# ============================================
# 등록된 복약 시간 확인
# ============================================

def _times(schedule, preferences):
    defaults = {
        "morning": "08:30",
        "lunch": "13:30",
        "dinner": "19:30",
    }

    mapping = {}

    for key, default in defaults.items():
        mapping[key] = (
            _time(
                (preferences or {}).get(key)
            )
            or default
        )

    reverse = {
        "아침": mapping["morning"],
        "점심": mapping["lunch"],
        "저녁": mapping["dinner"],
    }

    times = schedule.get("times")

    if not isinstance(times, list) or not times:
        default_slots = {
            1: ["아침"],
            2: ["아침", "저녁"],
            3: ["아침", "점심", "저녁"],
        }

        try:
            daily_count = int(
                schedule.get("dailyCount") or 0
            )
        except (TypeError, ValueError):
            daily_count = 0

        times = default_slots.get(
            daily_count,
            [],
        )

    result = []

    for item in times:
        value = _time(
            reverse.get(str(item), item)
        )

        if value and value not in result:
            result.append(value)

    return result


# ============================================
# 복약 기간 및 상태 확인
# ============================================

def _active(schedule, target_day):
    if schedule.get("active") is False:
        return False

    if schedule.get("isStock") is True:
        return False

    if schedule.get("asNeeded") is True:
        return False

    start = (
        _day(schedule.get("startDate"))
        or _day(schedule.get("createdAt"))
    )

    end = _day(
        schedule.get("endDate")
    )

    if start and target_day < start:
        return False

    if end and target_day > end:
        return False

    if not end and start:
        try:
            period = int(
                schedule.get("period") or 0
            )

            if period > 0:
                last_day = start + timedelta(
                    days=period - 1
                )

                if target_day > last_day:
                    return False

        except (TypeError, ValueError):
            pass

    return True


# ============================================
# 복용 완료 기록 조회
# ============================================

def _logs(user_id, target_day):
    docs = (
        db.collection("logs")
        .where("userId", "==", user_id)
        .where(
            "date",
            "==",
            target_day.isoformat(),
        )
        .stream()
    )

    return [
        doc.to_dict() or {}
        for doc in docs
    ]


def _taken(
    logs,
    schedule_id,
    medicine_name,
    scheduled_time,
):
    for log in logs:
        if log.get("taken") is False:
            continue

        log_sid = str(
            log.get("scheduleId") or ""
        )

        if log_sid and log_sid != schedule_id:
            continue

        if not log_sid:
            if (
                str(log.get("medicineName") or "")
                != medicine_name
            ):
                continue

        exact_time = _time(
            log.get("scheduledTime")
        )

        if exact_time:
            if exact_time == scheduled_time:
                return True

            continue

        saved_time = _time(
            log.get("time")
        )

        if saved_time == scheduled_time:
            return True

        meal = log.get("meal")

        if (
            meal
            and str(meal) == _meal(scheduled_time)
        ):
            return True

        if (
            saved_time
            and _meal(saved_time)
            == _meal(scheduled_time)
        ):
            return True

    return False


# ============================================
# Firebase FCM 푸시
# ============================================

def _push_tokens(user_id):
    user = _user(user_id)

    tokens = user.get("fcmTokens") or []

    return [
        token
        for token in tokens
        if isinstance(token, str)
        and token.strip()
    ][:30]


def _send_push(
    user_id,
    title,
    body,
    notification_id,
):
    sent = 0

    for token in _push_tokens(user_id):
        try:
            message = messaging.Message(
                token=token,
                notification=messaging.Notification(
                    title=title,
                    body=body,
                ),
                data={
                    "notificationId":
                        notification_id,
                    "type": "medication",
                },
                android=messaging.AndroidConfig(
                    priority="high",
                ),
            )

            messaging.send(message)
            sent += 1

        except Exception as exc:
            print(
                "[NOTIFICATION FCM ERROR]",
                type(exc).__name__,
                str(exc)[:180],
            )

    return sent


# ============================================
# 알림 생성 + 중복 방지
# ============================================

def _create_notification(
    user_id,
    schedule_id,
    kind,
    target_day,
    scheduled_time,
    title,
    body,
    medicine_name,
    dedupe_day=None,
):
    notification_id = _hash(
        "v3",
        user_id,
        schedule_id,
        kind,
        (
            dedupe_day or target_day
        ).isoformat(),
        scheduled_time,
    )

    ref = (
        db.collection(COLLECTION)
        .document(notification_id)
    )

    try:
        ref.create({
            "userId": user_id,
            "type": kind,
            "title": title,
            "body": body,
            "medicineName": medicine_name,
            "scheduleId": schedule_id,
            "scheduledDate":
                target_day.isoformat(),
            "scheduledTime": scheduled_time,
            "createdAt": now_kst(),
            "isRead": False,
            "readAt": None,
            "completedByMedication": False,
            "pushStatus": "pending",
        })

    except AlreadyExists:
        return False

    sent = _send_push(
        user_id,
        title,
        body,
        notification_id,
    )

    ref.update({
        "pushStatus":
            "sent" if sent
            else "no_device_or_failed",
        "pushSentCount": sent,
    })

    return True


# ============================================
# 복용 완료 시 알림 상태 반영
# ============================================

def _reconcile_taken(
    user_id,
    target_day,
    logs,
):
    changed = 0

    docs = (
        db.collection(COLLECTION)
        .where("userId", "==", user_id)
        .stream()
    )

    for snap in docs:
        notification = snap.to_dict() or {}

        if notification.get("type") not in (
            "before_30",
            "missed_30",
            "medication",
        ):
            continue

        if notification.get("isRead") is True:
            continue

        if (
            notification.get("scheduledDate")
            != target_day.isoformat()
        ):
            continue

        schedule_id = str(
            notification.get("scheduleId") or ""
        )

        scheduled_time = _time(
            notification.get("scheduledTime")
        )

        if not schedule_id or not scheduled_time:
            continue

        medicine_name = str(
            notification.get("medicineName") or ""
        )

        if _taken(
            logs,
            schedule_id,
            medicine_name,
            scheduled_time,
        ):
            snap.reference.update({
                "isRead": True,
                "readAt": now_kst(),
                "completedByMedication": True,
            })

            changed += 1

    return changed


# ============================================
# 사용자별 알림 생성
# ============================================

def _run_for_user(user_id, now):
    user = _user(user_id)

    target_day = now.date()

    logs = _logs(
        user_id,
        target_day,
    )

    created = []

    _reconcile_taken(
        user_id,
        target_day,
        logs,
    )

    schedules = (
        db.collection("schedules")
        .where("userId", "==", user_id)
        .stream()
    )

    for snap in schedules:
        schedule = snap.to_dict() or {}

        if not _active(
            schedule,
            target_day,
        ):
            continue

        schedule_id = snap.id

        medicine_name = str(
            schedule.get("medicineName")
            or "약"
        )

        times = _times(
            schedule,
            user.get("medicationTimes") or {},
        )

        for clock in times:
            hour, minute = map(
                int,
                clock.split(":"),
            )

            due = datetime(
                now.year,
                now.month,
                now.day,
                hour,
                minute,
                tzinfo=KST,
            )

            if _taken(
                logs,
                schedule_id,
                medicine_name,
                clock,
            ):
                continue

            before = due - timedelta(
                minutes=30
            )

            after = due + timedelta(
                minutes=30
            )

            notifications = [
                (
                    "before_30",
                    before,
                    "복약 30분 전",
                    (
                        f"{medicine_name} "
                        f"복용 시간이 30분 남았습니다. "
                        f"({clock})"
                    ),
                ),
                (
                    "missed_30",
                    after,
                    "복약 여부 확인",
                    (
                        f"{medicine_name} "
                        "복용 예정 시간에서 "
                        "30분이 지났습니다. "
                        "복용 여부를 확인해 주세요."
                    ),
                ),
            ]

            for (
                kind,
                trigger,
                title,
                body,
            ) in notifications:

                # 작업 실행이 조금 지연되는 경우 허용
                if (
                    trigger <= now
                    < trigger + timedelta(minutes=2)
                ):
                    if _create_notification(
                        user_id,
                        schedule_id,
                        kind,
                        target_day,
                        clock,
                        title,
                        body,
                        medicine_name,
                    ):
                        created.append({
                            "scheduleId": schedule_id,
                            "type": kind,
                        })

        # ====================================
        # 장기복용약 잔량 부족 알림
        # ====================================

        config = (
            schedule.get("longTermNotification")
            or {}
        )

        if config.get("enabled") is not True:
            continue

        # 오전 9시경 검사
        if (
            now.hour != 9
            or now.minute not in (0, 1)
        ):
            continue

        try:
            remaining = float(
                schedule.get("remainingCount")
            )

            per_dose = float(
                schedule.get("dosage")
            )

            per_day = int(
                schedule.get("dailyCount")
            )

            threshold = int(
                config.get(
                    "thresholdDays",
                    7,
                )
            )

            if (
                remaining < 0
                or per_dose <= 0
                or per_day <= 0
                or threshold < 1
            ):
                continue

            days_left = (
                remaining
                / (per_dose * per_day)
            )

        except (ValueError, TypeError):
            continue

        if days_left > threshold:
            continue

        # 7일 간격으로 재알림
        window_start = target_day - timedelta(
            days=target_day.toordinal() % 7
        )

        title = "장기복용약 잔량 안내"

        body = (
            f"{medicine_name}이(가) "
            f"약 {days_left:.1f}일분 남았습니다. "
            "처방 의료기관의 진료 시기를 "
            "확인해 주세요."
        )

        if _create_notification(
            user_id,
            schedule_id,
            "refill",
            target_day,
            "09:00",
            title,
            body,
            medicine_name,
            dedupe_day=window_start,
        ):
            created.append({
                "scheduleId": schedule_id,
                "type": "refill",
            })

    return created


# ============================================
# 요청 모델
# ============================================

class ReadRequest(BaseModel):
    userId: str


class TokenRequest(BaseModel):
    userId: str
    token: str = Field(
        min_length=30,
        max_length=4096,
    )


class LongTermRequest(BaseModel):
    userId: str
    enabled: bool = True
    thresholdDays: int = Field(
        default=7,
        ge=1,
        le=30,
    )


# ============================================
# 기기 토큰 등록
# ============================================

@router.post("/devices/register")
def register_device(req: TokenRequest):
    _user(req.userId)

    db.collection("users").document(
        req.userId
    ).update({
        "fcmTokens": firestore.ArrayUnion(
            [req.token]
        )
    })

    return {
        "success": True,
    }


@router.post("/devices/unregister")
def unregister_device(req: TokenRequest):
    _user(req.userId)

    db.collection("users").document(
        req.userId
    ).update({
        "fcmTokens": firestore.ArrayRemove(
            [req.token]
        )
    })

    return {
        "success": True,
    }


# ============================================
# 장기복용약 알림 설정
# ============================================

@router.patch("/long-term/{schedule_id}")
def configure_long_term(
    schedule_id: str,
    req: LongTermRequest,
):
    snap = (
        db.collection("schedules")
        .document(schedule_id)
        .get()
    )

    if (
        not snap.exists
        or (snap.to_dict() or {}).get("userId")
        != req.userId
    ):
        raise HTTPException(
            status_code=404,
            detail="복약 일정을 찾을 수 없습니다.",
        )

    snap.reference.update({
        "longTermNotification": {
            "enabled": req.enabled,
            "thresholdDays": req.thresholdDays,
        }
    })

    return {
        "success": True,
        "scheduleId": schedule_id,
        "enabled": req.enabled,
        "thresholdDays": req.thresholdDays,
    }


# ============================================
# 자동 실행 API
# ============================================

@router.post("/jobs/run")
def run_job(
    x_job_secret: Optional[str] = Header(
        default=None
    ),
):
    secret = os.getenv(
        "NOTIFICATION_JOB_SECRET",
        "",
    )

    if (
        not secret
        or not x_job_secret
        or not hmac.compare_digest(
            secret,
            x_job_secret,
        )
    ):
        raise HTTPException(
            status_code=403,
            detail="작업 실행 권한이 없습니다.",
        )

    now = now_kst()

    created = []

    for doc in db.collection("users").stream():
        try:
            created.extend(
                _run_for_user(
                    doc.id,
                    now,
                )
            )

        except Exception as exc:
            print(
                "[NOTIFICATION USER ERROR]",
                doc.id,
                type(exc).__name__,
                str(exc)[:180],
            )

    return {
        "success": True,
        "createdCount": len(created),
        "created": created,
        "checkedAt": now.isoformat(),
    }


# ============================================
# 기존 Flutter 알림 목록 API
# ============================================

@router.get("/user/{user_id}")
def get_notifications(
    user_id: str,
    limit: int = Query(
        default=100,
        ge=1,
        le=300,
    ),
):
    _user(user_id)

    target_day = now_kst().date()

    logs = _logs(
        user_id,
        target_day,
    )

    _reconcile_taken(
        user_id,
        target_day,
        logs,
    )

    cutoff = now_kst() - timedelta(
        days=RETENTION_DAYS
    )

    rows = []

    docs = (
        db.collection(COLLECTION)
        .where("userId", "==", user_id)
        .stream()
    )

    for snap in docs:
        notification = snap.to_dict() or {}

        created = _dt(
            notification.get("createdAt")
        )

        if created and created < cutoff:
            snap.reference.delete()
            continue

        read_at = _dt(
            notification.get("readAt")
        )

        rows.append({
            "id": snap.id,
            "title":
                notification.get("title", "알림"),
            "body":
                notification.get("body", ""),
            "type":
                notification.get("type", ""),
            "isRead":
                bool(notification.get(
                    "isRead", False
                )),
            "createdAt":
                created.isoformat()
                if created else None,
            "readAt":
                read_at.isoformat()
                if read_at else None,
        })

    rows.sort(
        key=lambda row:
            row["createdAt"] or "",
        reverse=True,
    )

    rows = rows[:limit]

    return {
        "success": True,
        "count": len(rows),
        "unreadCount": sum(
            not row["isRead"]
            for row in rows
        ),
        "notifications": rows,
    }


# ============================================
# 알림 개별 읽음 처리
# ============================================

@router.patch("/{notification_id}/read")
def mark_read(
    notification_id: str,
    req: ReadRequest,
):
    snap = (
        db.collection(COLLECTION)
        .document(notification_id)
        .get()
    )

    if not snap.exists:
        raise HTTPException(
            status_code=404,
            detail="알림을 찾을 수 없습니다.",
        )

    if (
        (snap.to_dict() or {}).get("userId")
        != req.userId
    ):
        raise HTTPException(
            status_code=403,
            detail="접근 권한이 없습니다.",
        )

    snap.reference.update({
        "isRead": True,
        "readAt": now_kst(),
    })

    return {
        "success": True,
    }


# ============================================
# 알림 전체 읽음 처리
# ============================================

@router.patch("/user/{user_id}/read-all")
def mark_all_read(user_id: str):
    _user(user_id)

    changed = 0
    batch_count = 0
    batch = db.batch()

    docs = (
        db.collection(COLLECTION)
        .where("userId", "==", user_id)
        .stream()
    )

    for snap in docs:
        if (
            (snap.to_dict() or {}).get("isRead")
            is True
        ):
            continue

        batch.update(
            snap.reference,
            {
                "isRead": True,
                "readAt": now_kst(),
            },
        )

        changed += 1
        batch_count += 1

        if batch_count >= 400:
            batch.commit()
            batch = db.batch()
            batch_count = 0

    if batch_count:
        batch.commit()

    return {
        "success": True,
        "updatedCount": changed,
    }
