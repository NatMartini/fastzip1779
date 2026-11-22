import hashlib
import hmac
import secrets
import time
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session as DBSession

from fastzip.config import settings
from fastzip.db import database
from fastzip.models import RateLimit, Session, User, now

hasher = PasswordHasher()
dummy_hash = hasher.hash(secrets.token_urlsafe(24))
SESSION_COOKIE = "fastzip_session"
CSRF_COOKIE = "fastzip_csrf"


def digest(value: str) -> str:
    return hmac.new(settings().session_secret.encode(), value.encode(), hashlib.sha256).hexdigest()


def set_cookie(response: Response, name: str, value: str, max_age: int):
    response.set_cookie(
        name,
        value,
        httponly=True,
        secure=settings().secure_cookies,
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def new_csrf(response: Response) -> str:
    nonce = secrets.token_urlsafe(32)
    token = nonce + "." + digest("csrf:" + nonce)
    set_cookie(response, CSRF_COOKIE, token, 86400)
    return token


def csrf(request: Request):
    token = request.headers.get("x-csrf-token", "")
    cookie = request.cookies.get(CSRF_COOKIE, "")
    nonce, _, signature = token.partition(".")
    if (
        not token
        or not hmac.compare_digest(token, cookie)
        or not hmac.compare_digest(signature, digest("csrf:" + nonce))
    ):
        raise HTTPException(403, "Invalid CSRF token; refresh the page and try again")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != settings().public_origin.rstrip("/"):
        raise HTTPException(403, "Untrusted request origin")
    if request.headers.get("sec-fetch-site") == "cross-site":
        raise HTTPException(403, "Cross-site request rejected")


def user(request: Request, db: DBSession = Depends(database)) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    session = db.get(Session, digest("session:" + token)) if token else None
    if session is None or session.expires_at <= now():
        raise HTTPException(401, "Please sign in")
    account = db.get(User, session.user_id)
    if account is None:
        raise HTTPException(401, "Please sign in")
    return account


def admin(account: User = Depends(user)) -> User:
    if account.role != "admin":
        raise HTTPException(403, "Administrator access required")
    return account


def create_session(db: DBSession, account: User, response: Response):
    token = secrets.token_urlsafe(48)
    lifetime = settings().session_days * 86400
    db.add(
        Session(
            token_hash=digest("session:" + token),
            user_id=account.id,
            expires_at=now() + timedelta(seconds=lifetime),
        )
    )
    set_cookie(response, SESSION_COOKIE, token, lifetime)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return hasher.verify(hashed, password)
    except (VerificationError, InvalidHashError):
        return False


def rate_limit(db: DBSession, key: str, limit: int, seconds: int):
    window = int(time.time()) // seconds * seconds
    statement = insert(RateLimit).values(key=digest(key), window_start=window, count=1)
    count = db.scalar(
        statement.on_conflict_do_update(
            index_elements=[RateLimit.key, RateLimit.window],
            set_={"count": RateLimit.count + 1},
        ).returning(RateLimit.count)
    )
    db.commit()  # Failed authentication attempts count across every API replica.
    if count > limit:
        raise HTTPException(
            429,
            "Too many attempts; please try again later",
            headers={"Retry-After": str(seconds - int(time.time()) % seconds)},
        )


def active_session(db: DBSession, request: Request):
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        return db.scalar(select(Session).where(Session.token_hash == digest("session:" + token)))
    return None
