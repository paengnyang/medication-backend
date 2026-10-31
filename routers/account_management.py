import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from google.cloud import firestore

from firebase_config import db
from services.password_service import (
    verify_user_password,
    hash_password,
)

router = APIRouter(prefix="/users", tags=["Users - Account Management"])



class SecureProfileUpdate(BaseModel):
    currentPassword: str = Field(min_length=1)

    # 새 비밀번호 (입력한 경우에만 변경)
    newPassword: Optional[str] = Field(
        default=None,
        min_length=8,
    )

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


def _verified_user(user_id: str, password: str):
    ref = db.collection("users").document(user_id)
    snapshot = ref.get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    user = snapshot.to_dict() or {}
    if not verify_user_password(user, password):
        raise HTTPException(status_code=403, detail="현재 비밀번호가 올바르지 않습니다.")
    return ref, user



@router.patch("/{user_id}/profile-secure")
def update_profile_secure(
    user_id: str,
    request: SecureProfileUpdate,
):
    # 1. 현재 비밀번호 확인
    ref, user = _verified_user(
        user_id,
        request.currentPassword,
    )

    # 2. 일반 개인정보 추출
    # 비밀번호는 일반 필드로 저장하지 않음
    values = request.model_dump(
        exclude_unset=True,
        exclude={
            "currentPassword",
            "newPassword",
        },
    )

    # 3. 이름 검증
    if "name" in values:
        values["name"] = str(
            values["name"] or ""
        ).strip()

        if not values["name"]:
            raise HTTPException(
                status_code=422,
                detail="이름을 입력해주세요.",
            )

    # 4. 전화번호 처리
    if "phone" in values and values["phone"] is not None:
        values["phone"] = str(values["phone"]).strip()

    # 5. 알레르기 목록 정리
    if "allergyList" in values:
        values["allergyList"] = list(
            dict.fromkeys(
                str(item).strip()
                for item in (values["allergyList"] or [])
                if str(item).strip()
            )
        )

    # 6. 새 비밀번호 변경
    password_changed = False

    if request.newPassword is not None:
        new_password = request.newPassword

        # 기존 비밀번호와 동일한지 확인
        if verify_user_password(user, new_password):
            raise HTTPException(
                status_code=400,
                detail="새 비밀번호는 기존 비밀번호와 달라야 합니다.",
            )

        # 해시 처리 후 저장
        values["passwordHash"] = hash_password(
            new_password
        )

        password_changed = True

    # 7. 수정할 내용 확인
    if not values:
        raise HTTPException(
            status_code=400,
            detail="수정할 개인정보가 없습니다.",
        )

    # 8. Firestore 업데이트
    values["updatedAt"] = datetime.now(timezone.utc)

    ref.update(values)

    return {
        "success": True,
        "message": "개인정보 수정 완료",
        "userId": user_id,
        "passwordChanged": password_changed,
    }


def _refs_by_user_id(collection: str, user_id: str):
    return [doc.reference for doc in db.collection(collection).where("userId", "==", user_id).stream()]


def _delete_in_batches(refs):
    count = 0
    for start in range(0, len(refs), 400):
        batch = db.batch()
        chunk = refs[start:start+400]
        for ref in chunk:
            batch.delete(ref)
        batch.commit()
        count += len(chunk)
    return count


@router.delete("/{user_id}/account")
def delete_account(user_id: str, request: AccountDeletionRequest):
    if request.confirmation != "회원탈퇴":
        raise HTTPException(status_code=400, detail="회원탈퇴 확인 문구가 일치하지 않습니다.")
    user_ref, user = _verified_user(user_id, request.currentPassword)
    email = str(user.get("email") or "").strip().lower()

    # Delete owned medication data while account still exists. Do not delete
    # guardian accounts or other users' records here.
    schedule_refs = _refs_by_user_id("schedules", user_id)
    schedule_ids = [ref.id for ref in schedule_refs]
    log_refs = _refs_by_user_id("logs", user_id)
    # Some historical logs have only scheduleId, not userId.
    for i in range(0, len(schedule_ids), 30):
        chunk = schedule_ids[i:i+30]
        if chunk:
            log_refs.extend(doc.reference for doc in db.collection("logs")
                            .where("scheduleId", "in", chunk).stream())

    refs = schedule_refs + log_refs
    for collection in ("stockMedicines", "password_reset_codes"):
        refs.extend(_refs_by_user_id(collection, user_id))
    # De-duplicate refs in case logs matched both predicates.
    refs = list({ref.path: ref for ref in refs}.values())
    deleted = _delete_in_batches(refs)

    # Maintain email uniqueness index atomically with account deletion.
    email_index = db.collection("registered_emails").document(
        hashlib.sha256(email.encode("utf-8")).hexdigest()
    ) if email else None
    transaction = db.transaction()

    @firestore.transactional
    def remove_account(transaction):
        current = user_ref.get(transaction=transaction)
        if not current.exists:
            raise HTTPException(status_code=404, detail="이미 탈퇴한 사용자입니다.")
        if not verify_user_password(current.to_dict() or {}, request.currentPassword):
            raise HTTPException(status_code=403, detail="현재 비밀번호가 올바르지 않습니다.")
        index = email_index.get(transaction=transaction) if email_index else None
        transaction.delete(user_ref)
        if index and index.exists and (index.to_dict() or {}).get("userId") == user_id:
            transaction.delete(email_index)

    remove_account(transaction)
    return {"success": True, "message": "회원탈퇴 완료", "deletedRecordCount": deleted}
