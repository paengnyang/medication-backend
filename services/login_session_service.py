"""Opaque, revocable login sessions. Never store a raw bearer token in Firestore."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from firebase_config import db

COLLECTION = 'login_sessions'
LIFETIME_DAYS = 30


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def create_login_session(user_id: str) -> str:
    token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    db.collection(COLLECTION).document(_digest(token)).set({
        'userId': user_id,
        'createdAt': now,
        'expiresAt': now + timedelta(days=LIFETIME_DAYS),
    })
    return token


def _require_token(authorization: str | None) -> str:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(status_code=401, detail='로그인이 필요합니다.')
    token = authorization[7:].strip()
    if not token or len(token) > 512:
        raise HTTPException(status_code=401, detail='유효하지 않은 세션입니다.')
    return token


def get_session_user_id(authorization: str | None) -> str:
    token = _require_token(authorization)
    doc = db.collection(COLLECTION).document(_digest(token)).get()
    if not doc.exists:
        raise HTTPException(status_code=401, detail='로그인이 만료되었습니다.')
    session = doc.to_dict() or {}
    expires = session.get('expiresAt')
    now = datetime.now(timezone.utc)
    if not isinstance(expires, datetime):
        raise HTTPException(status_code=401, detail='유효하지 않은 세션입니다.')
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires <= now:
        doc.reference.delete()
        raise HTTPException(status_code=401, detail='로그인이 만료되었습니다.')
    user_id = str(session.get('userId') or '').strip()
    if not user_id:
        raise HTTPException(status_code=401, detail='유효하지 않은 세션입니다.')
    return user_id


def revoke_login_session(authorization: str | None) -> None:
    token = _require_token(authorization)
    db.collection(COLLECTION).document(_digest(token)).delete()
