"""Работа в контейнере: проверка состояния, идентификатор экземпляра, остановка."""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import db, healthcheck
from app.config import Settings, instance_id

Server = Callable[[int], str]


class Handler(BaseHTTPRequestHandler):
    """Отвечает на любой GET кодом, заданным серверу."""

    def do_GET(self) -> None:
        self.send_response(self.server.answer)  # type: ignore[attr-defined]
        self.end_headers()

    def log_message(self, *args: object) -> None:
        """Без вывода в журнал тестов."""


@pytest.fixture
def server() -> Iterator[Server]:
    """Запуск локального HTTP-сервера с нужным кодом ответа; возвращает его порт."""
    started: list[HTTPServer] = []

    def start(answer: int) -> str:
        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        httpd.answer = answer  # type: ignore[attr-defined]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        started.append(httpd)
        return str(httpd.server_address[1])

    yield start
    for httpd in started:
        httpd.shutdown()
        httpd.server_close()


def free_port() -> str:
    """Порт, на котором заведомо никто не слушает."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return str(probe.getsockname()[1])


# ---------------------------------------------------------------------------
# python -m app.healthcheck — команда HEALTHCHECK образа
# ---------------------------------------------------------------------------
def test_health_url_uses_app_port(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_PORT", "9123")
    assert healthcheck.health_url() == "http://127.0.0.1:9123/healthz"
    monkeypatch.delenv("APP_PORT")
    assert healthcheck.health_url() == "http://127.0.0.1:8000/healthz"


def test_check_ok(server: Server) -> None:
    assert healthcheck.check(f"http://127.0.0.1:{server(200)}/healthz") is True


@pytest.mark.parametrize("answer", [204, 503])
def test_check_fails_unless_200(server: Server, answer: int) -> None:
    assert healthcheck.check(f"http://127.0.0.1:{server(answer)}/healthz") is False


def test_check_fails_without_server() -> None:
    assert healthcheck.check(f"http://127.0.0.1:{free_port()}/healthz") is False


def test_main_exit_code(
    server: Server, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("APP_PORT", server(200))
    assert healthcheck.main() == 0
    assert capsys.readouterr().out == "ok\n"

    monkeypatch.setenv("APP_PORT", free_port())
    assert healthcheck.main() == 1
    assert "нет ответа 200" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Идентификатор экземпляра и закрытие соединений при остановке
# ---------------------------------------------------------------------------
def test_instance_id() -> None:
    assert instance_id(Settings(instance_id=" app-2 ")) == "app-2"
    assert instance_id(Settings(instance_id="")) == socket.gethostname()
    assert instance_id() == socket.gethostname()


def test_dispose_without_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(db, "_connection", None)
    db.dispose()  # приложение остановили до первого подключения — ошибки нет
