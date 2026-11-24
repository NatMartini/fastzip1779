from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession

from fastzip.config import settings
from fastzip.db import database
from fastzip.models import User
from fastzip.security import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    active_session,
    create_session,
    csrf,
    dummy_hash,
    hasher,
    new_csrf,
    rate_limit,
    user,
    verify_password,
)

router = APIRouter(prefix="/api/auth")


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


def account_json(account: User):
    return {"id": str(account.id), "email": account.email, "role": account.role}


@router.get("/csrf")
def get_csrf(response: Response):
    return {"csrf_token": new_csrf(response)}


@router.post("/register", status_code=201, dependencies=[Depends(csrf)])
def register(
    body: Credentials, request: Request, response: Response, db: DBSession = Depends(database)
):
    ip = request.client.host if request.client else "unknown"
    rate_limit(db, "register:" + ip, settings().registration_rate_limit, 3600)
    account = User(
        email=str(body.email).lower(), password_hash=hasher.hash(body.password), role="user"
    )
    db.add(account)
    try:
        db.flush()
        create_session(db, account, response)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "An account with that email already exists") from exc
    return account_json(account)


@router.post("/login", dependencies=[Depends(csrf)])
def login(
    body: Credentials, request: Request, response: Response, db: DBSession = Depends(database)
):
    ip = request.client.host if request.client else "unknown"
    email = str(body.email).lower()
    rate_limit(db, "login-ip:" + ip, settings().auth_rate_limit * 3, 900)
    rate_limit(db, "login-account:" + email, settings().auth_rate_limit, 900)
    account = db.scalar(select(User).where(User.email == email))
    if (
        not verify_password(body.password, account.password_hash if account else dummy_hash)
        or account is None
    ):
        raise HTTPException(401, "Email or password is incorrect")
    if hasher.check_needs_rehash(account.password_hash):
        account.password_hash = hasher.hash(body.password)
    # Rotate the current browser's session to prevent session fixation and abandoned tokens.
    if old := active_session(db, request):
        db.delete(old)
    create_session(db, account, response)
    db.commit()
    return account_json(account)


@router.post("/logout", status_code=204, dependencies=[Depends(csrf)])
def logout(
    request: Request,
    response: Response,
    db: DBSession = Depends(database),
    account: User = Depends(user),
):
    if session := active_session(db, request):
        db.delete(session)
        db.commit()
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings().secure_cookies, httponly=True, samesite="lax"
    )
    response.delete_cookie(
        CSRF_COOKIE, path="/", secure=settings().secure_cookies, httponly=True, samesite="lax"
    )


@router.get("/me")
def me(account: User = Depends(user)):
    return account_json(account)
