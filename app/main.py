"""Точка входа: сборка приложения, служебный адрес и обработка ошибок."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app import db, web
from app.api import router as api_router
from app.config import get_settings, hide_password
from app.rules import AppError
from app.web import router as web_router

logger = logging.getLogger("warehouse")

VERSION = "0.1.0"


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
    if not db.is_configured():  # в тестах подключение настраивается заранее
        db.setup()
    yield


app = FastAPI(
    title="Складской учёт",
    version=VERSION,
    description="Учёт материалов, поставщиков, единиц измерения и поступлений на склад.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=str(Path(__file__).parent / "static")), name="static")
app.include_router(api_router)
app.include_router(web_router)


@app.get("/healthz", tags=["Служебные"], summary="Проверка работоспособности")
def healthz() -> Response:
    """Состояние приложения и доступность базы данных."""
    database_ok = db.database_is_available()
    return JSONResponse(
        {
            "status": "ok" if database_ok else "degraded",
            "version": VERSION,
            "database": "ok" if database_ok else "unavailable",
        },
        status_code=200 if database_ok else 503,
    )


def wants_json(request: Request) -> bool:
    """Программному интерфейсу отвечаем JSON, страницам — HTML."""
    return request.url.path.startswith(("/api/", "/healthz"))


@app.exception_handler(AppError)
async def handle_app_error(request: Request, error: Exception) -> Response:
    """Ошибки предметной области: понятное сообщение вместо трассировки."""
    assert isinstance(error, AppError)
    if wants_json(request):
        return JSONResponse(error.payload(), status_code=error.status)
    if error.status == 401:
        return RedirectResponse("/login", status_code=303)
    return web.page(
        request,
        "error.html",
        status_code=error.status,
        status=error.status,
        error_message=error.message,
        rule=error.rule,
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, error: Exception) -> Response:
    """Некорректный запрос: перечисляем поля, которые не прошли проверку."""
    assert isinstance(error, RequestValidationError)
    fields = [".".join(str(part) for part in item["loc"][1:]) for item in error.errors()]
    message = "Некорректные данные запроса: " + ", ".join(field for field in fields if field)
    if wants_json(request):
        return JSONResponse({"error": {"code": "BAD_REQUEST", "message": message}}, status_code=422)
    return web.page(
        request, "error.html", status_code=422, status=422, error_message=message, rule=None
    )


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, error: Exception) -> Response:
    """Непредвиденная ошибка: пользователю — общее сообщение, подробности — в журнал."""
    logger.exception("Необработанная ошибка при обработке %s", request.url.path)
    message = "Внутренняя ошибка приложения. Повторите операцию позднее."
    if wants_json(request):
        return JSONResponse(
            {"error": {"code": "INTERNAL_ERROR", "message": message}}, status_code=500
        )
    return web.page(
        request, "error.html", status_code=500, status=500, error_message=message, rule=None
    )


def main() -> Any:
    """Запуск сервера: make run."""
    import uvicorn

    settings = get_settings()
    print(f"База данных: {hide_password(settings.database_url)}")  # noqa: T201
    uvicorn.run("app.main:app", host=settings.app_host, port=settings.app_port, reload=True)


if __name__ == "__main__":
    main()
