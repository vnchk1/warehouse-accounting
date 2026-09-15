"""Авторизация: хеширование пароля и сеанс в подписанном файле cookie."""

from __future__ import annotations

import base64
import hmac
import json
import time
from hashlib import sha256
from typing import Annotated

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Request, Response
from sqlalchemy.orm import Session

from app import rules
from app.config import Settings, get_settings
from app.db import get_session
from app.models import User

SESSION_COOKIE = "warehouse_session"
_hasher = PasswordHasher()

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[Session, Depends(get_session)]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ---------------------------------------------------------------------------
# Файл cookie сеанса: {"uid": 1, "ts": 1789...} + подпись HMAC-SHA256
# ---------------------------------------------------------------------------
def _sign(secret: str, payload: str) -> str:
    digest = hmac.new(secret.encode(), payload.encode(), sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def _encode(data: dict[str, int]) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(value: str) -> dict[str, int]:
    padding = "=" * (-len(value) % 4)
    return json.loads(base64.urlsafe_b64decode(value + padding))


def start_session(response: Response, user: User, settings: Settings) -> None:
    payload = _encode({"uid": user.id, "ts": int(time.time())})
    response.set_cookie(
        SESSION_COOKIE,
        f"{payload}.{_sign(settings.secret_key, payload)}",
        max_age=settings.session_hours * 3600,
        httponly=True,
        samesite="lax",
    )


def end_session(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE)


def _user_id_from_cookie(request: Request, settings: Settings) -> int | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token or token.count(".") != 1:
        return None
    payload, signature = token.split(".")
    if not hmac.compare_digest(signature, _sign(settings.secret_key, payload)):
        return None
    try:
        data = _decode(payload)
        if time.time() - int(data["ts"]) > settings.session_hours * 3600:
            return None
        return int(data["uid"])
    except (ValueError, KeyError, TypeError):
        return None


def find_user(request: Request, db: SessionDep, settings: SettingsDep) -> User | None:
    """Пользователь сеанса или None (страница входа)."""
    user_id = _user_id_from_cookie(request, settings)
    user = db.get(User, user_id) if user_id else None
    request.state.user = user
    return user


def current_user(user: Annotated[User | None, Depends(find_user)]) -> User:
    """Пользователь сеанса; иначе — ошибка 401."""
    if user is None:
        raise rules.unauthorized("Требуется вход в Систему")
    return user


CurrentUser = Annotated[User, Depends(current_user)]
OptionalUser = Annotated[User | None, Depends(find_user)]
