import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Header, HTTPException
from google.cloud import firestore
from pydantic import BaseModel, Field

from firebase_config import db
from services.login_session_service import get_session_user_id
from services.password_service import hash_password, verify_user_password

router = APIRouter(prefix="/users", tags=["Users - Account Management"])


class SecureProfileUpdate(BaseModel):
    currentPassword: str = Field(min_length=1)
    newPassword: Optional[str] = Field(default=None, min_length=8)
    name: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    pregnancy: Optional[bool] = None
    birthYear: Optional[int] = None
    birthMonth: Optional[int] = Field(default=None, ge=1, le=12)
    birthDay: Optional[int] = Field(default=None, ge=1, le=31)
    allergyList: Optional[list[str]] = None


class AccountDeletionRequest(BaseModel):
    currentPassword: str = Field(min_length=1)
    confirmation: str


def _verified_user(user_id: str, password: str, authorization: Optional[str]):
    # Critical: possession of another user's ID/password is not enough to
    # operate on their account; bearer token must belong to this user.
    session_user_id = get_session_user_id(authorization)
    if session_user_id != user_id:
        raise HTTPException(status_code=403, detail="본인 계정만 수정하거나 탈퇴할 수 있습니다.")

    ref = db.collection("users").document(user_id)
    snap = ref.get()
    if not snap.exists:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    user = snap.to_dict() or {}
    if not verify_user_password(user, password):
        raise HTTPException(status_code=403, detail="현재 비밀번호가 올바르지 않습니다.")
    return ref, user


@router.patch("/{user_id}/profile-secure")
def update_profile_secure(
    user_id: str,
    request: SecureProfileUpdate,
    authorization: Optional[str] = Header(default=None),
):
    ref, user = _verified_user(user_id, request.currentPassword, authorization)
    values = request.model_dump(exclude_unset=True, exclude={"currentPassword", "newPassword"})
    if "name" in values:
        values["name"] = str(values["name"] or "").strip()
        if not values["name"]:
            raise HTTPException(status_code=422, detail="이름을 입력해주세요.")
    if "phone" in values and values["phone"] is not None:
        values["phone"] = str(values["phone"]).strip()
    if "allergyList" in values:
        values["allergyList"] = list(dict.fromkeys(
            str(item).strip() for item in (values["allergyList"] or [])
            if str(item).strip()
        ))

    password_changed = request.newPassword is not None
    if password_changed:
        if verify_user_password(user, request.newPassword):
            raise HTTPException(status_code=400, detail="새 비밀번호는 기존 비밀번호와 달라야 합니다.")
        values["passwordHash"] = hash_password(request.newPassword)

    if not values:
        raise HTTPException(status_code=400, detail="수정할 개인정보가 없습니다.")
    values["updatedAt"] = datetime.now(timezone.utc)
    ref.update(values)

    # Optional session invalidation on password change is a separate security
    # policy. The current token must remain usable until client re-login flow
    # is implemented; see README.
    return {
        "success": True, "message": "개인정보 수정 완료", "userId": user_id,
        "passwordChanged": password_changed,
    }


def _query_refs(collection: str, field: str, value: str):
    return [doc.reference for doc in db.collection(collection).where(field, "==", value).stream()]


def _delete_refs(refs):
    unique = list({ref.path: ref for ref in refs}.values())
    for start in range(0, len(unique), 400):
        batch = db.batch()
        for ref in unique[start:start + 400]:
            batch.delete(ref)
        batch.commit()
    return len(unique)


@router.delete("/{user_id}/account")
def delete_account(
    user_id: str,
    request: AccountDeletionRequest,
    authorization: Optional[str] = Header(default=None),
):
    if request.confirmation != "회원탈퇴":
        raise HTTPException(status_code=400, detail="회원탈퇴 확인 문구가 일치하지 않습니다.")
    user_ref, user = _verified_user(user_id, request.currentPassword, authorization)

    # Collect user-owned data. These names are based on project code reviewed;
    # audit all backend collections and storage before production release.
    schedules = _query_refs("schedules", "userId", user_id)
    schedule_ids = [ref.id for ref in schedules]
    refs = list(schedules)
    for collection in (
        "logs", "medicationLogs", "stockMedicines", "stocks", "notifications",
        "password_reset_codes",
    ):
        refs.extend(_query_refs(collection, "userId", user_id))

    # Some legacy logs only have scheduleId; clean those up too.
    for pos in range(0, len(schedule_ids), 30):
        refs.extend(doc.reference for doc in db.collection("logs")
                    .where("scheduleId", "in", schedule_ids[pos:pos + 30]).stream())

    # A guardian may link to many wards and vice versa; delete only links,
    # never guardian / ward user documents.
    for collection in ("guardian_links", "guardian_link_codes"):
        refs.extend(_query_refs(collection, "wardUserId", user_id))
        refs.extend(_query_refs(collection, "guardianUserId", user_id))
    # guardian_link_codes may additionally use userId for creator.
    refs.extend(_query_refs("guardian_link_codes", "userId", user_id))

    session_refs = _query_refs("login_sessions", "userId", user_id)

    # NOTE: multi-batch Firestore deletes are not atomic. If a batch fails,
    # the account remains (with partial child cleanup). Retry is necessary.
    # Deleting account last avoids confirming full account deletion too early.
    # Session refs are deleted AFTER account commit so a failed cleanup does
    # not lock the user out before the final account removal.
    deleted_count = _delete_refs(refs)

    email = str(user.get("email") or "").strip().lower()
    email_ref = (db.collection("registered_emails").document(
        hashlib.sha256(email.encode("utf-8")).hexdigest()) if email else None)

    transaction = db.transaction()

    @firestore.transactional
    def remove_account(tx):
        current = user_ref.get(transaction=tx)
        if not current.exists:
            raise HTTPException(status_code=404, detail="이미 탈퇴한 사용자입니다.")
        if not verify_user_password(current.to_dict() or {}, request.currentPassword):
            raise HTTPException(status_code=403, detail="현재 비밀번호가 올바르지 않습니다.")
        index = email_ref.get(transaction=tx) if email_ref else None
        tx.delete(user_ref)
        if index and index.exists and (index.to_dict() or {}).get("userId") == user_id:
            tx.delete(email_ref)

    remove_account(transaction)
    # Revoke all sessions, including other devices.
    deleted_count += _delete_refs(session_refs)
    return {"success": True, "message": "회원탈퇴 완료", "deletedRecordCount": deleted_count}
