import hashlib
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from google.api_core.exceptions import AlreadyExists
from firebase_config import db
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix='/notifications', tags=['Notifications'])
KST = timezone(timedelta(hours=9))
RETENTION_DAYS = 7
TAKEN_LOGS_COLLECTION = os.getenv('TAKEN_LOGS_COLLECTION', 'medicationLogs')


def _now():
    return datetime.now(KST)


def _time(value, medication_times):
    value = str(value or '').strip()
    mapping = {
        '아침': medication_times.get('morning', '08:30'),
        '점심': medication_times.get('lunch', '13:30'),
        '저녁': medication_times.get('dinner', '19:30'),
    }
    value = str(mapping.get(value, value)).strip()
    try:
        return datetime.strptime(value, '%H:%M').strftime('%H:%M')
    except ValueError:
        return None


def _schedule_times(schedule, medication_times):
    times = schedule.get('times')
    if not isinstance(times, list) or not times:
        try:
            count = int(schedule.get('dailyCount') or 0)
        except (ValueError, TypeError):
            count = 0
        times = {1: ['아침'], 2: ['아침', '저녁'], 3: ['아침', '점심', '저녁']}.get(count, [])
    result = []
    for item in times:
        t = _time(item, medication_times)
        if t and t not in result:
            result.append(t)
    return result


def _meal(value):
    value = str(value or '').strip()
    if value in ('아침', '점심', '저녁'):
        return value
    try:
        hour = int(value.split(':')[0])
        minute = int(value.split(':')[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
    except (ValueError, IndexError):
        return None
    if hour < 11:
        return '아침'
    return '점심' if hour < 17 else '저녁'


def _taken(schedule_id, time, logs):
    for log in logs:
        if log.get('taken') is False:
            continue
        if str(log.get('scheduleId') or '') != str(schedule_id):
            continue
        explicit = log.get('scheduledTime')
        if explicit:
            if str(explicit).strip() == time:
                return True
            continue
        saved_meal = str(log.get('meal') or '').strip()
        if saved_meal:
            if saved_meal == _meal(time):
                return True
        elif _meal(log.get('time')) == _meal(time):
            return True
    return False


def _active(schedule, date):
    if schedule.get('active') is False:
        return False
    if schedule.get('isStock') is True or schedule.get('asNeeded') is True:
        return False
    start = str(schedule.get('startDate') or '')[:10]
    end = str(schedule.get('endDate') or '')[:10]
    return (not start or start <= date) and (not end or date <= end)


# ==================================================
# 알림 동기화
# ==================================================

def _sync_notifications(user_id):
    now = _now()
    today = now.strftime('%Y-%m-%d')
    current_time = now.strftime('%H:%M')

    # 사용자 조회
    user_doc = (
        db.collection('users')
        .document(user_id)
        .get()
    )

    if not user_doc.exists:
        raise HTTPException(
            status_code=404,
            detail='사용자를 찾을 수 없습니다.',
        )

    user_data = user_doc.to_dict() or {}
    medication_times = (
        user_data.get('medicationTimes') or {}
    )

    # 실제 복약 일정
    schedules = list(
        db.collection('schedules')
        .where('userId', '==', user_id)
        .stream()
    )

    # 실제 복용 완료 기록: medicationLogs
    log_docs = (
        db.collection(TAKEN_LOGS_COLLECTION)
        .where('userId', '==', user_id)
        .where('date', '==', today)
        .stream()
    )

    today_logs = [
        doc.to_dict() or {}
        for doc in log_docs
    ]

    notification_collection = (
        db.collection('notifications')
    )

    # 기존 알림을 포함해 7일 지난 기록 삭제
    expiration = now - timedelta(
        days=RETENTION_DAYS
    )

    existing_notifications = list(
        notification_collection
        .where('userId', '==', user_id)
        .stream()
    )

    for doc in existing_notifications:
        data = doc.to_dict() or {}
        created_at = data.get('createdAt')

        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(
                    tzinfo=timezone.utc
                )

            if created_at < expiration:
                doc.reference.delete()

    # 복약 일정별 오늘 알림 처리
    for schedule_doc in schedules:
        schedule = schedule_doc.to_dict() or {}

        if not _active(schedule, today):
            continue

        schedule_id = str(
            schedule.get('scheduleId')
            or schedule_doc.id
        )

        medicine_name = str(
            schedule.get('medicineName')
            or '등록된 약'
        )

        times = _schedule_times(
            schedule,
            medication_times,
        )

        for scheduled_time in times:
            unique_value = (
                f'{user_id}|{schedule_id}|'
                f'{today}|{scheduled_time}'
            )

            notification_id = hashlib.sha256(
                unique_value.encode('utf-8')
            ).hexdigest()

            notification_ref = (
                notification_collection
                .document(notification_id)
            )

            # 해당 시간대에 이미 복용했는지 확인
            is_taken = _taken(
                schedule_id,
                scheduled_time,
                today_logs,
            )

            if is_taken:
                existing = notification_ref.get()

                if existing.exists:
                    notification_data = (
                        existing.to_dict() or {}
                    )

                    if not notification_data.get('isRead'):
                        notification_ref.update({
                            'isRead': True,
                            'readAt': now,
                            'completedByMedication': True,
                        })

                # 복용 완료했다면 새 알림 생성 안 함
                continue

            # 아직 복약 시간이 되지 않음
            if scheduled_time > current_time:
                continue

            scheduled_at = datetime.strptime(
                f'{today} {scheduled_time}',
                '%Y-%m-%d %H:%M',
            ).replace(tzinfo=KST)

            notification_data = {
                'userId': user_id,
                'scheduleId': schedule_id,
                'medicineName': medicine_name,
                'type': 'medication',
                'title': '복약 알림',
                'body': (
                    f'{scheduled_time} 복약 시간입니다. '
                    f'{medicine_name} 복용 일정을 '
                    '확인해주세요.'
                ),
                'scheduledDate': today,
                'scheduledTime': scheduled_time,
                'createdAt': scheduled_at,
                'isRead': False,
                'readAt': None,
                'completedByMedication': False,
            }

            try:
                notification_ref.create(
                    notification_data
                )
            except AlreadyExists:
                # 중복 알림은 생성하지 않음
                pass


# ==================================================
# 알림 목록 조회
# ==================================================

@router.get('/user/{user_id}')
def get_notifications(user_id: str):

    _sync_notifications(user_id)

    docs = (
        db.collection('notifications')
        .where('userId', '==', user_id)
        .stream()
    )

    notifications = []

    for doc in docs:
        data = doc.to_dict() or {}

        created_at = data.get('createdAt')

        notifications.append({
            'id': doc.id,
            'title': data.get('title', '알림'),
            'body': data.get('body', ''),
            'type': data.get('type', ''),
            'isRead': data.get('isRead', False),
            'createdAt': (
                created_at.isoformat()
                if isinstance(created_at, datetime)
                else None
            ),
        })

    notifications.sort(
        key=lambda item: item['createdAt'] or '',
        reverse=True,
    )

    unread_count = sum(
        1
        for item in notifications
        if not item['isRead']
    )

    return {
        'success': True,
        'count': len(notifications),
        'unreadCount': unread_count,
        'notifications': notifications,
    }


# ==================================================
# 개별 알림 읽음 처리
# ==================================================

class MarkReadRequest(BaseModel):
    userId: str


@router.patch('/{notification_id}/read')
def mark_notification_read(
    notification_id: str,
    request: MarkReadRequest,
):
    ref = (
        db.collection('notifications')
        .document(notification_id)
    )

    doc = ref.get()

    if not doc.exists:
        raise HTTPException(
            status_code=404,
            detail='알림을 찾을 수 없습니다.',
        )

    data = doc.to_dict() or {}

    if data.get('userId') != request.userId:
        raise HTTPException(
            status_code=403,
            detail='접근 권한이 없습니다.',
        )

    ref.update({
        'isRead': True,
        'readAt': _now(),
    })

    return {
        'success': True,
        'message': '알림을 확인했습니다.',
    }


# ==================================================
# 전체 알림 읽음 처리
# ==================================================

@router.patch('/user/{user_id}/read-all')
def mark_all_notifications_read(user_id: str):

    docs = (
        db.collection('notifications')
        .where('userId', '==', user_id)
        .stream()
    )

    batch = db.batch()
    pending = 0
    updated_count = 0

    for doc in docs:
        data = doc.to_dict() or {}

        if data.get('isRead') is True:
            continue

        batch.update(
            doc.reference,
            {
                'isRead': True,
                'readAt': _now(),
            },
        )

        pending += 1
        updated_count += 1

        if pending >= 400:
            batch.commit()
            batch = db.batch()
            pending = 0

    if pending:
        batch.commit()

    return {
        'success': True,
        'updatedCount': updated_count,
    }
