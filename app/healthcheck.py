"""Проверка состояния для HEALTHCHECK контейнера: python -m app.healthcheck.

Код возврата 0 — приложение отвечает и база данных доступна (/healthz → 200),
иначе 1. Отдельная программа вместо curl: в образе нет лишних утилит, а Python
в нём есть всегда.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

#: Сколько ждать ответа, секунд (меньше timeout проверки в compose.yaml)
TIMEOUT = 3


def health_url() -> str:
    """Адрес проверки внутри контейнера: сам контейнер, порт приложения."""
    return f"http://127.0.0.1:{os.environ.get('APP_PORT', '8000')}/healthz"


def check(url: str | None = None) -> bool:
    """True, если /healthz ответил кодом 200."""
    address = url or health_url()
    try:
        # Безопасно: адрес формирует сама программа, схема всегда http://127.0.0.1
        with urllib.request.urlopen(address, timeout=TIMEOUT) as response:  # nosec B310
            return response.status == 200
    except (urllib.error.URLError, OSError, ValueError):
        # 503 (база недоступна) — тоже URLError: urlopen считает его ошибкой HTTP
        return False


def main() -> int:
    if check():
        print("ok")
        return 0
    print(f"нет ответа 200 от {health_url()}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
