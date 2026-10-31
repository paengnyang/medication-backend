from datetime import date, datetime, timezone, timedelta
from typing import Literal
import hashlib

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from firebase_admin import firestore
from firebase_config import db

router = APIRouter(prefix="/prescription-groups", tags=["Prescription Groups"])

DEFAULT_TIMES = {"morning": "08:30", "lunch": "13:30", "dinner": "19:30"}


class CreateGroup(BaseModel):
    userId: str = Field(min_length=1)
    alias: str = Field(min_length=1, max_length=40)
    scheduleIds: list[str] = Field(min_length=1, max_length=100)


class RenameGroup(BaseModel):
    userId: str = Field(min_length=1)
    alias: str = Field(min_length=1, max_length=40)


class TakeGroup(BaseModel):
    userId: str = Field(min_length=1)
    date: date
    timeSlot: Literal["morning", "lunch", "dinner"]
    excludedScheduleIds: list[str] = Field(default_factory=list)


def _user(user_id):
    snap = db.collection("users").document(user_id).get()
    if not snap.exists:
        raise HTTPException(404, "사용자를 찾을 수 없습니다.")
    return snap.to_dict() or {}


def _group(group_id, user_id):
    ref = db.collection("prescriptionGroups").document(group_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(404, "복약 그룹을 찾을 수 없습니다.")
    data = snap.to_dict() or {}
    if data.get("userId") != user_id:
        raise HTTPException(403, "본인의 복약 그룹만 사용할 수 있습니다.")
    return ref, data


def _time_for_slot(user, slot):
    configured = user.get("medicationTimes") or {}
    time = str(configured.get(slot) or DEFAULT_TIMES[slot]).strip()
    try:
        datetime.strptime(time, "%H:%M")
    except ValueError:
        raise HTTPException(400, f"{slot} 복약 시간 설정이 잘못되었습니다.")
    return time


def _eligible(group, user_id, day, time):
    """Only schedules explicitly belonging to this group at the exact configured time."""
    result = []
    ids = group.get("scheduleIds") or []
    for sid in ids:
        snap = db.collection("schedules").document(sid).get()
        if not snap.exists:
            continue
        s = snap.to_dict() or {}
        if s.get("userId") != user_id or s.get("active") is False:
            continue
        if s.get("isStock") is True or s.get("asNeeded") is True:
            continue
        start = str(s.get("startDate") or s.get("createdAt") or "")[:10]
        end = str(s.get("endDate") or "")[:10]
        if start and start > day:
            continue
        if end and end < day:
            continue
        if not end and start:
            try:
                period = int(s.get("period") or 0)
                if period > 0:
                    last_day = (date.fromisoformat(start) + timedelta(days=period - 1)).isoformat()
                    if day > last_day:
                        continue
            except (ValueError, TypeError):
                pass
        # Only explicitly registered clock times are eligible; never infer from dailyCount.
        times = [str(x).strip() for x in (s.get("times") or [])]
        if time not in times:
            continue
        result.append({"scheduleId": sid, "medicineName": s.get("medicineName", ""),
                       "time": time, "dosage": float(s.get("dosage") or 1)})
    return result


def _existing_logs(user_id, day, time, ids):
    """Read legacy randomly-IDed logs and the new deterministic ones alike."""
    if not ids:
        return set()
    docs = (db.collection("logs").where("userId", "==", user_id)
            .where("date", "==", day).stream())
    return {str(d.to_dict().get("scheduleId")) for d in docs
            if (d.to_dict() or {}).get("taken") is True
            and str((d.to_dict() or {}).get("time", "")).strip() == time
            and (d.to_dict() or {}).get("scheduleId") in ids}


def _selection(group, user_id, day, slot):
    user = _user(user_id)
    scheduled_time = _time_for_slot(user, slot)
    eligible = _eligible(group, user_id, day, scheduled_time)
    existing = _existing_logs(user_id, day, scheduled_time,
                              {x["scheduleId"] for x in eligible})
    for s in eligible:
        s["alreadyTaken"] = s["scheduleId"] in existing
    return scheduled_time, eligible


@router.post("", status_code=201)
def create_group(body: CreateGroup):
    _user(body.userId)
    alias = body.alias.strip()
    ids = list(dict.fromkeys(x.strip() for x in body.scheduleIds if x.strip()))
    if not alias or len(ids) != len(body.scheduleIds):
        raise HTTPException(400, "별칭과 중복 없는 복약 일정 ID가 필요합니다.")
    for sid in ids:
        snap = db.collection("schedules").document(sid).get()
        data = snap.to_dict() if snap.exists else None
        if not data or data.get("userId") != body.userId:
            raise HTTPException(400, f"본인 소유의 복약 일정이 아닙니다: {sid}")
        if data.get("isStock") or data.get("asNeeded"):
            raise HTTPException(400, "상비약/필요 시 복용약은 일괄 복약 그룹에서 제외해주세요.")
    ref = db.collection("prescriptionGroups").document()
    ref.set({"groupId": ref.id, "userId": body.userId, "alias": alias,
             "scheduleIds": ids, "createdAt": datetime.now(timezone.utc),
             "active": True})
    return {"success": True, "groupId": ref.id, "alias": alias,
            "scheduleIds": ids}


@router.get("")
def list_groups(userId: str = Query(min_length=1)):
    _user(userId)
    docs = db.collection("prescriptionGroups").where("userId", "==", userId).stream()
    groups = []
    for d in docs:
        row = d.to_dict() or {}
        if row.get("active") is not False:
            groups.append({"groupId": d.id, "alias": row.get("alias"),
                           "scheduleIds": row.get("scheduleIds", [])})
    return {"success": True, "groups": groups}


@router.patch("/{group_id}/alias")
def rename_group(group_id: str, body: RenameGroup):
    ref, group = _group(group_id, body.userId)
    if not body.alias.strip():
        raise HTTPException(400, "별칭을 입력해주세요.")
    ref.update({"alias": body.alias.strip(), "updatedAt": datetime.now(timezone.utc)})
    return {"success": True, "groupId": group_id, "alias": body.alias.strip()}


@router.post("/{group_id}/preview")
def preview_group(group_id: str, body: TakeGroup):
    _, group = _group(group_id, body.userId)
    if group.get("active") is False:
        raise HTTPException(400, "종료된 그룹입니다.")
    time, items = _selection(group, body.userId, body.date.isoformat(), body.timeSlot)
    excluded = set(body.excludedScheduleIds)
    return {"success": True, "groupId": group_id, "alias": group.get("alias"),
            "date": body.date.isoformat(), "timeSlot": body.timeSlot,
            "scheduledTime": time, "medicines": items,
            "willCheck": [x for x in items if not x["alreadyTaken"]
                          and x["scheduleId"] not in excluded]}


@router.post("/{group_id}/take")
def take_group(group_id: str, body: TakeGroup):
    _, group = _group(group_id, body.userId)
    if group.get("active") is False:
        raise HTTPException(400, "종료된 그룹입니다.")
    day = body.date.isoformat()
    time, items = _selection(group, body.userId, day, body.timeSlot)
    selected_ids = {x["scheduleId"] for x in items}
    excluded = set(body.excludedScheduleIds)
    if excluded - selected_ids:
        raise HTTPException(400, "복용 제외 목록에 그룹 외 약이 포함되어 있습니다.")
    targets = [x for x in items if not x["alreadyTaken"] and x["scheduleId"] not in excluded]
    if not items:
        return {"success": True, "checkedCount": 0, "reason": "해당 시간대의 복용 대상 약이 없습니다."}
    if not targets:
        return {"success": True, "checkedCount": 0,
                "reason": "복용 대상이 이미 완료되었거나 사용자가 제외했습니다."}

    # One transaction prevents duplicate clicks among requests using THIS endpoint.
    # The existing /logs/taken endpoint must use the same deterministic IDs to make
    # concurrency fully safe across both endpoints (see integration note).
    ref_pairs = []
    for item in targets:
        sid = item["scheduleId"]
        key = hashlib.sha256(f"{body.userId}|{sid}|{day}|{time}".encode()).hexdigest()
        ref_pairs.append((item, db.collection("logs").document("dose_" + key)))

    tx = db.transaction()

    @firestore.transactional
    def commit(transaction):
        # All reads must precede writes in Firestore transactions.
        snaps = [(item, ref, ref.get(transaction=transaction)) for item, ref in ref_pairs]
        schedule_refs = [db.collection("schedules").document(item["scheduleId"]) for item, _, _ in snaps]
        schedule_snaps = [ref.get(transaction=transaction) for ref in schedule_refs]
        made = []
        for (item, log_ref, log_snap), sref, ssnap in zip(snaps, schedule_refs, schedule_snaps):
            if log_snap.exists and (log_snap.to_dict() or {}).get("taken") is True:
                continue
            if not ssnap.exists:
                continue
            schedule = ssnap.to_dict() or {}
            if schedule.get("userId") != body.userId or schedule.get("active") is False:
                continue
            remaining = float(schedule.get("remainingCount") or 0)
            amount = float(schedule.get("dosage") or 1)
            if remaining < amount:
                continue
            log_data = {"userId": body.userId, "scheduleId": item["scheduleId"],
                        "medicineName": item["medicineName"], "date": day, "time": time,
                        "taken": True, "takenAt": datetime.now(timezone.utc),
                        "source": "PRESCRIPTION_GROUP", "groupId": group_id}
            transaction.set(log_ref, log_data)
            transaction.update(sref, {"remainingCount": remaining - amount})
            made.append(item["scheduleId"])
        return made

    try:
        created = commit(tx)
    except Exception as exc:
        raise HTTPException(500, "복약 기록 저장 중 오류가 발생했습니다.") from exc
    return {"success": True, "groupId": group_id, "date": day,
            "timeSlot": body.timeSlot, "scheduledTime": time,
            "checkedCount": len(created), "checkedScheduleIds": created,
            "alreadyTakenCount": sum(x["alreadyTaken"] for x in items),
            "excludedCount": len(excluded)}
