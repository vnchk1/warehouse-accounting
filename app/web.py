"""Графический интерфейс: экранные формы на шаблонах Jinja2.

Формы отправляются обычным способом (POST) и после успешной операции
выполняется переход на страницу списка. При ошибке страница отображается
заново с сообщением и с уже введёнными значениями.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app import rules, services
from app.auth import CurrentUser, OptionalUser, SessionDep, SettingsDep, end_session, start_session
from app.models import ROLE_NAMES

router = APIRouter(include_in_schema=False)
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals["role_names"] = ROLE_NAMES


def page(request: Request, template: str, status_code: int = 200, **context: Any) -> Any:
    """Формирование страницы: пользователь и сообщение доступны всем шаблонам."""
    context.setdefault("user", getattr(request.state, "user", None))
    context.setdefault("message", request.query_params.get("msg"))
    context.setdefault("error", None)
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def go(url: str, message: str | None = None) -> RedirectResponse:
    """Переход после успешной операции."""
    return RedirectResponse(f"{url}?msg={message}" if message else url, status_code=303)


# ---------------------------------------------------------------------------
# Разбор значений форм отбора
#
# Поля отбора необязательны, и браузер отправляет незаполненное поле пустой
# строкой. Пустое значение означает «без ограничения», поэтому параметры
# принимаются строкой и разбираются здесь, а не проверкой типа в маршруте.
# ---------------------------------------------------------------------------
def opt_date(value: str | None, *, field: str, title: str) -> date | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise rules.AppError(
            f"Поле «{title}»: дата указывается в виде ГГГГ-ММ-ДД", field=field
        ) from None


def req_date(value: str | None, *, field: str, title: str) -> date:
    parsed = opt_date(value, field=field, title=title)
    if parsed is None:
        raise rules.AppError(f"Поле «{title}» обязательно для заполнения", field=field)
    return parsed


def opt_id(value: str | None) -> int | None:
    """Идентификатор из списка выбора; пустое значение — «все»."""
    text = (value or "").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


# ---------------------------------------------------------------------------
# Вход и выход
# ---------------------------------------------------------------------------
@router.get("/login")
def login_form(request: Request, user: OptionalUser) -> Any:
    return go("/") if user else page(request, "login.html", login="")


@router.post("/login")
def login(
    request: Request,
    db: SessionDep,
    settings: SettingsDep,
    login: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Any:
    try:
        user = services.authenticate(db, login, password)
    except rules.AppError as error:
        return page(
            request, "login.html", status_code=error.status, error=error.message, login=login
        )
    response = go("/")
    start_session(response, user, settings)
    return response


@router.post("/logout")
def logout(user: CurrentUser) -> Any:
    response = go("/login", "Сеанс завершён")
    end_session(response)
    return response


@router.get("/")
def index(user: CurrentUser) -> Any:
    return go("/receipts")


# ---------------------------------------------------------------------------
# Поставщики
# ---------------------------------------------------------------------------
def _suppliers_page(
    request: Request,
    db: SessionDep,
    user: Any,
    edit: int | None = None,
    error: str | None = None,
    values: dict[str, Any] | None = None,
) -> Any:
    """Страница поставщиков; при edit=<id> форма заполняется для изменения."""
    editing = services.get_supplier(db, edit) if edit else None
    if values is None:
        values = (
            {"name": editing.name, "inn": editing.inn, "phone": editing.phone or ""}
            if editing
            else {}
        )
    return page(
        request,
        "suppliers.html",
        status_code=422 if error else 200,
        error=error,
        suppliers=services.list_suppliers(db),
        editing=editing,
        values=values,
    )


@router.get("/suppliers")
def suppliers_page(
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    edit: Annotated[str | None, Query()] = None,
) -> Any:
    return _suppliers_page(request, db, user, edit=opt_id(edit))


@router.post("/suppliers")
def suppliers_save(
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    name: Annotated[str, Form()],
    inn: Annotated[str, Form()],
    phone: Annotated[str, Form()] = "",
    supplier_id: Annotated[str, Form()] = "",
) -> Any:
    values = {"name": name, "inn": inn, "phone": phone}
    edit_id = int(supplier_id) if supplier_id.isdigit() else None
    try:
        services.save_supplier(db, name=name, inn=inn, phone=phone, supplier_id=edit_id)
    except rules.AppError as error:
        return _suppliers_page(request, db, user, edit=edit_id, error=error.message, values=values)
    return go("/suppliers", "Поставщик сохранён")


@router.post("/suppliers/{supplier_id}/delete")
def suppliers_delete(request: Request, db: SessionDep, user: CurrentUser, supplier_id: int) -> Any:
    try:
        services.delete_supplier(db, supplier_id)
    except rules.AppError as error:
        return _suppliers_page(request, db, user, error=error.message)
    return go("/suppliers", "Поставщик удалён")


# ---------------------------------------------------------------------------
# Материалы
# ---------------------------------------------------------------------------
def _materials_page(
    request: Request,
    db: SessionDep,
    user: Any,
    edit: int | None = None,
    error: str | None = None,
    values: dict[str, Any] | None = None,
    search: str = "",
) -> Any:
    """Страница материалов; при edit=<id> форма заполняется для изменения."""
    editing = services.get_material(db, edit) if edit else None
    if values is None:
        values = (
            {"sku": editing.sku, "name": editing.name, "okei_code": editing.unit.okei_code}
            if editing
            else {}
        )
    return page(
        request,
        "materials.html",
        status_code=422 if error else 200,
        error=error,
        materials=services.list_materials(db, search=search),
        okei=sorted(rules.OKEI.items()),
        editing=editing,
        values=values,
        search=search,
    )


@router.get("/materials")
def materials_page(
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    edit: Annotated[str | None, Query()] = None,
    q: Annotated[str, Query()] = "",
) -> Any:
    return _materials_page(request, db, user, edit=opt_id(edit), search=q)


@router.post("/materials")
def materials_save(
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    sku: Annotated[str, Form()],
    name: Annotated[str, Form()],
    okei_code: Annotated[str, Form()],
    material_id: Annotated[str, Form()] = "",
) -> Any:
    values = {"sku": sku, "name": name, "okei_code": okei_code}
    edit_id = int(material_id) if material_id.isdigit() else None
    try:
        services.save_material(
            db,
            sku=sku,
            name=name,
            okei_code=okei_code,
            material_id=edit_id,
        )
    except rules.AppError as error:
        return _materials_page(request, db, user, edit=edit_id, error=error.message, values=values)
    return go("/materials", "Материал сохранён")


@router.post("/materials/{material_id}/delete")
def materials_delete(request: Request, db: SessionDep, user: CurrentUser, material_id: int) -> Any:
    try:
        services.delete_material(db, material_id)
    except rules.AppError as error:
        return _materials_page(request, db, user, error=error.message)
    return go("/materials", "Материал удалён")


# ---------------------------------------------------------------------------
# Поступления
# ---------------------------------------------------------------------------
def _receipts_page(
    request: Request,
    db: SessionDep,
    user: Any,
    date_from: date | None = None,
    date_to: date | None = None,
    supplier_id: int | None = None,
    error: str | None = None,
) -> Any:
    """Страница перечня поступлений с отбором по периоду и поставщику."""
    return page(
        request,
        "receipts.html",
        status_code=422 if error else 200,
        error=error,
        receipts=services.list_receipts(
            db, date_from=date_from, date_to=date_to, supplier_id=supplier_id
        ),
        suppliers=services.list_suppliers(db),
        filters={"date_from": date_from, "date_to": date_to, "supplier_id": supplier_id},
        today=date.today(),
    )


@router.get("/receipts")
def receipts_page(
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    date_from: Annotated[str | None, Query()] = None,
    date_to: Annotated[str | None, Query()] = None,
    supplier_id: Annotated[str | None, Query()] = None,
) -> Any:
    try:
        since = opt_date(date_from, field="date_from", title="с")
        until = opt_date(date_to, field="date_to", title="по")
    except rules.AppError as error:
        return _receipts_page(request, db, user, error=error.message)
    return _receipts_page(
        request, db, user, date_from=since, date_to=until, supplier_id=opt_id(supplier_id)
    )


@router.post("/receipts")
def receipts_create(
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    receipt_date: Annotated[str, Form()] = "",
    supplier_id: Annotated[str, Form()] = "",
) -> Any:
    try:
        receipt = services.create_receipt(
            db,
            user,
            receipt_date=req_date(receipt_date, field="receipt_date", title="Дата"),
            supplier_id=opt_id(supplier_id) or 0,
        )
    except rules.AppError as error:
        return _receipts_page(request, db, user, error=error.message)
    return go(f"/receipts/{receipt.id}", "Поступление создано")


def _receipt_page(
    request: Request, db: SessionDep, user: Any, receipt_id: int, error: str | None = None
) -> Any:
    """Карточка поступления со строками."""
    return page(
        request,
        "receipt.html",
        status_code=422 if error else 200,
        error=error,
        receipt=services.get_receipt(db, receipt_id),
        materials=services.list_materials(db),
    )


@router.get("/receipts/{receipt_id}")
def receipt_page(request: Request, db: SessionDep, user: CurrentUser, receipt_id: int) -> Any:
    return _receipt_page(request, db, user, receipt_id)


@router.post("/receipts/{receipt_id}/items")
def receipt_add_item(
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    receipt_id: int,
    material_id: Annotated[str, Form()],
    quantity: Annotated[str, Form()],
    price: Annotated[str, Form()],
) -> Any:
    try:
        services.add_item(
            db,
            receipt_id=receipt_id,
            material_id=int(material_id) if material_id.isdigit() else 0,
            quantity=quantity,
            price=price,
        )
    except rules.AppError as error:
        return _receipt_page(request, db, user, receipt_id, error=error.message)
    return go(f"/receipts/{receipt_id}", "Строка добавлена")


@router.post("/receipts/{receipt_id}/items/{item_id}/delete")
def receipt_delete_item(
    request: Request, db: SessionDep, user: CurrentUser, receipt_id: int, item_id: int
) -> Any:
    try:
        services.delete_item(db, receipt_id=receipt_id, item_id=item_id)
    except rules.AppError as error:
        return _receipt_page(request, db, user, receipt_id, error=error.message)
    return go(f"/receipts/{receipt_id}", "Строка удалена")


@router.post("/receipts/{receipt_id}/delete")
def receipt_delete(request: Request, db: SessionDep, user: CurrentUser, receipt_id: int) -> Any:
    try:
        services.delete_receipt(db, receipt_id)
    except rules.AppError as error:
        return _receipt_page(request, db, user, receipt_id, error=error.message)
    return go("/receipts", "Поступление удалено")


# ---------------------------------------------------------------------------
# Отчёт по поставкам
# ---------------------------------------------------------------------------
@router.get("/report")
def report_page(
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    date_from: Annotated[str | None, Query()] = None,
    date_to: Annotated[str | None, Query()] = None,
    supplier_id: Annotated[str | None, Query()] = None,
) -> Any:
    """Отчёт по поставкам. Незаполненный отбор означает «за всё время и по всем
    поставщикам», поэтому пустые значения полей — это не ошибка."""
    chosen = opt_id(supplier_id)
    try:
        since = opt_date(date_from, field="date_from", title="с")
        until = opt_date(date_to, field="date_to", title="по")
        report = services.receipts_report(db, date_from=since, date_to=until, supplier_id=chosen)
    except rules.AppError as error:
        return page(
            request,
            "report.html",
            status_code=error.status,
            error=error.message,
            report=None,
            suppliers=services.list_suppliers(db),
            filters={"date_from": date_from or "", "date_to": date_to or "", "supplier_id": chosen},
        )
    return page(
        request,
        "report.html",
        report=report,
        suppliers=services.list_suppliers(db),
        filters={"date_from": since, "date_to": until, "supplier_id": chosen},
    )


# ---------------------------------------------------------------------------
# Пользователи (только администратор)
# ---------------------------------------------------------------------------
def _users_page(
    request: Request,
    db: SessionDep,
    user: Any,
    error: str | None = None,
    values: dict[str, Any] | None = None,
) -> Any:
    """Страница учётных записей (доступна только администратору)."""
    return page(
        request,
        "users.html",
        status_code=422 if error else 200,
        error=error,
        users=services.list_users(db, user),
        values=values or {"role": "storekeeper"},
    )


@router.get("/users")
def users_page(request: Request, db: SessionDep, user: CurrentUser) -> Any:
    return _users_page(request, db, user)


@router.post("/users")
def users_create(
    request: Request,
    db: SessionDep,
    user: CurrentUser,
    settings: SettingsDep,
    login: Annotated[str, Form()],
    full_name: Annotated[str, Form()],
    role: Annotated[str, Form()],
    password: Annotated[str, Form()],
) -> Any:
    try:
        services.create_user(
            db,
            user,
            login=login,
            full_name=full_name,
            role=role,
            password=password,
            password_min_length=settings.password_min_length,
        )
    except rules.AppError as error:
        return _users_page(
            request,
            db,
            user,
            error=error.message,
            values={"login": login, "full_name": full_name, "role": role},
        )
    return go("/users", "Пользователь создан")


@router.post("/users/{user_id}/delete")
def users_delete(request: Request, db: SessionDep, user: CurrentUser, user_id: int) -> Any:
    try:
        services.delete_user(db, user, user_id)
    except rules.AppError as error:
        return _users_page(request, db, user, error=error.message)
    return go("/users", "Пользователь удалён")
