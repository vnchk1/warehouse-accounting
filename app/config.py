"""Конфигурация приложения — только через переменные окружения (п. 6 задания)."""

from __future__ import annotations

import functools

from pydantic import Field, ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Параметры запуска. Значения читаются из окружения или из файла .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # обязательные
    database_url: str
    secret_key: str = Field(min_length=16)
    admin_login: str = "admin"
    admin_password: str = Field(min_length=8)

    # необязательные
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    session_hours: int = 12
    password_min_length: int = 8


@functools.lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Единственный экземпляр настроек. При ошибке приложение не запускается."""
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as error:
        lines = ["Не удалось прочитать конфигурацию:"]
        for item in error.errors():
            name = str(item["loc"][0]).upper()
            reason = "переменная не задана" if item["type"] == "missing" else item["msg"]
            lines.append(f"  {name}: {reason}")
        lines.append("Скопируйте .env.example в .env и заполните значения.")
        raise SystemExit("\n".join(lines)) from error


def hide_password(url: str) -> str:
    """Строка подключения без пароля — для вывода на экран."""
    if "@" not in url or "//" not in url:
        return url
    prefix, rest = url.split("//", 1)
    credentials, host = rest.split("@", 1)
    user = credentials.split(":", 1)[0]
    return f"{prefix}//{user}:***@{host}"
