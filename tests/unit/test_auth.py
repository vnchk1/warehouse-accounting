"""Сеанс в подписанном файле cookie (БП-09) и вспомогательные функции настроек."""

from __future__ import annotations

from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import Response

from app import auth
from app.config import Settings, get_settings, hide_password

SECRET = "unit-secret-key-0123456789"


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": "postgresql+psycopg://u:p@localhost/x_test",
        "secret_key": SECRET,
        "admin_password": "admin-password",
        "session_hours": 2,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]  # параметры из словаря


def request_with(token: str | None) -> Request:
    headers = [(b"cookie", f"{auth.SESSION_COOKIE}={token}".encode())] if token else []
    return Request({"type": "http", "headers": headers})


def issue(user_id: int, issued_at: int, secret: str = SECRET) -> str:
    payload = auth._encode({"uid": user_id, "ts": issued_at})
    return f"{payload}.{auth._sign(secret, payload)}"


def test_session_cookie_attributes() -> None:
    response = Response()
    user = type("Account", (), {"id": 7})()
    auth.start_session(response, user, settings())  # type: ignore[arg-type]  # нужен только id
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f"{auth.SESSION_COOKIE}=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Max-Age=7200" in cookie  # session_hours = 2

    token = cookie.split(";", 1)[0].split("=", 1)[1]
    assert auth._user_id_from_cookie(request_with(token), settings()) == 7


def test_end_session_deletes_cookie() -> None:
    response = Response()
    auth.end_session(response)
    cookie = response.headers["set-cookie"]
    assert cookie.startswith(f'{auth.SESSION_COOKIE}=""')
    assert "Max-Age=0" in cookie


def test_valid_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.time, "time", lambda: 1_000_000)
    assert auth._user_id_from_cookie(request_with(issue(5, 1_000_000)), settings()) == 5


def test_session_lifetime_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сеанс действует ровно session_hours часов, секундой позже — уже нет."""
    lifetime = 2 * 3600
    monkeypatch.setattr(auth.time, "time", lambda: 1_000_000 + lifetime)
    assert auth._user_id_from_cookie(request_with(issue(5, 1_000_000)), settings()) == 5
    monkeypatch.setattr(auth.time, "time", lambda: 1_000_000 + lifetime + 1)
    assert auth._user_id_from_cookie(request_with(issue(5, 1_000_000)), settings()) is None


@pytest.mark.parametrize(
    "token",
    [
        None,
        "",
        "без-точки",
        "a.b.c",
        issue(5, 1_000_000, secret="другой-ключ-0123456789"),  # чужая подпись
        issue(5, 1_000_000) + "x",  # подпись изменена
    ],
)
def test_rejected_tokens(token: str | None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(auth.time, "time", lambda: 1_000_000)
    assert auth._user_id_from_cookie(request_with(token), settings()) is None


@pytest.mark.parametrize(
    "payload", ["not-base64!", auth._encode({"ts": 4_000_000_000}), "e30", "bnVsbA"]
)
def test_signed_but_malformed_payload(payload: str) -> None:
    """Подпись верна, но содержимое испорчено: сеанс не принимается, без ошибки 500."""
    token = f"{payload}.{auth._sign(SECRET, payload)}"
    assert auth._user_id_from_cookie(request_with(token), settings()) is None


def test_encode_decode_roundtrip() -> None:
    data = {"uid": 123, "ts": 1_789_000_000}
    encoded = auth._encode(data)
    assert "=" not in encoded  # без выравнивания: значение пригодно для cookie
    assert auth._decode(encoded) == data


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql+psycopg://user:secret@db:5432/wh", "postgresql+psycopg://user:***@db:5432/wh"),
        ("postgresql://user@db/wh", "postgresql://user:***@db/wh"),
        ("sqlite:///file.db", "sqlite:///file.db"),
        ("user:secret@host", "user:secret@host"),
    ],
)
def test_hide_password(url: str, expected: str) -> None:
    assert hide_password(url) == expected


def test_missing_settings_are_reported(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Без обязательных переменных приложение не запускается и называет их."""
    monkeypatch.chdir(tmp_path)  # нет файла .env
    monkeypatch.delenv("DATABASE_URL")
    monkeypatch.setenv("SECRET_KEY", "короткий")
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit) as error:
            get_settings()
    finally:
        get_settings.cache_clear()
    message = str(error.value)
    assert "DATABASE_URL: переменная не задана" in message
    assert "SECRET_KEY:" in message
    assert ".env.example" in message


def test_settings_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("APP_HOST", "APP_PORT", "PASSWORD_MIN_LENGTH", "ADMIN_LOGIN", "SESSION_HOURS"):
        monkeypatch.delenv(name, raising=False)
    value = Settings(
        _env_file=None,  # type: ignore[call-arg]  # параметр pydantic-settings
        database_url="postgresql+psycopg://u@localhost/x_test",
        secret_key=SECRET,
        admin_password="admin-password",
    )
    assert value.session_hours == 12
    assert (value.app_host, value.app_port, value.password_min_length) == ("127.0.0.1", 8000, 8)
    assert value.admin_login == "admin"
