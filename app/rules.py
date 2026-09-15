"""Ошибки приложения и правила предметной области БП-01 … БП-10.

Модуль не зависит от базы данных и от веб-фреймворка: это чистые функции,
поэтому правила легко проверяются тестами. Каждая ошибка несёт код правила,
что позволяет проследить отказ до конкретного пункта технического задания.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

MIN_DATE = date(2000, 1, 1)
SKU_PATTERN = re.compile(r"^[A-Z0-9-]{3,32}$")
LOGIN_PATTERN = re.compile(r"^[a-z0-9_.-]{3,50}$")


# ---------------------------------------------------------------------------
# Ошибки
# ---------------------------------------------------------------------------
class AppError(Exception):
    """Ошибка, о которой нужно сообщить пользователю понятным текстом."""

    def __init__(
        self,
        message: str,
        *,
        status: int = 422,
        code: str = "VALIDATION_ERROR",
        rule: str | None = None,
        field: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code
        self.rule = rule
        self.field = field

    def payload(self) -> dict[str, Any]:
        """Тело ответа об ошибке для HTTP API."""
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.rule:
            error["rule"] = self.rule
        if self.field:
            error["field"] = self.field
        return {"error": error}


def not_found(message: str) -> AppError:
    return AppError(message, status=404, code="NOT_FOUND")


def conflict(message: str, rule: str | None = None) -> AppError:
    return AppError(message, status=409, code="CONFLICT", rule=rule)


def forbidden(message: str) -> AppError:
    return AppError(message, status=403, code="FORBIDDEN", rule="БП-09")


def unauthorized(message: str) -> AppError:
    return AppError(message, status=401, code="UNAUTHORIZED")


# ---------------------------------------------------------------------------
# БП-01. Единица измерения — только из ОКЕИ
# ---------------------------------------------------------------------------
#: Общероссийский классификатор единиц измерения ОК 015-94 (выборка):
#: код ОКЕИ, условное обозначение, наименование, только ли целые количества.
#: Коды взяты из классификатора; произвольные значения недопустимы, поэтому
#: единиц, которых в ОКЕИ нет (мешок, поддон, палета), в перечне тоже нет.
OKEI: dict[str, tuple[str, str, bool]] = {
    # Штучные и упаковочные единицы
    "642": ("ед", "Единица", True),
    "796": ("шт", "Штука", True),
    "797": ("100 шт", "Сто штук", False),
    "798": ("тыс. шт", "Тысяча штук", False),
    "641": ("дюжина", "Дюжина", True),
    "715": ("пар", "Пара", True),
    "704": ("набор", "Набор", True),
    "778": ("упак", "Упаковка", True),
    "839": ("компл", "Комплект", True),
    "812": ("ящ", "Ящик", True),
    "736": ("рул", "Рулон", True),
    "868": ("бут", "Бутылка", True),
    # Масса (метрические)
    "163": ("г", "Грамм", False),
    "166": ("кг", "Килограмм", False),
    "168": ("т", "Тонна", False),
    "206": ("ц", "Центнер", False),
    "162": ("кар", "Карат метрический", False),
    # Масса (американские)
    "186": ("фунт", "Фунт (0,45359237 кг)", False),
    "187": ("унция", "Унция (28,349523 г)", False),
    # Объём (метрические)
    "111": ("мл", "Миллилитр; кубический сантиметр", False),
    "112": ("л", "Литр; кубический дециметр", False),
    "113": ("м³", "Кубический метр", False),
    # Объём (американские)
    "145": ("галлон", "Галлон США (3,78541 л)", False),
    "146": ("баррель", "Баррель США (158,988 л)", False),
    # Длина (метрические)
    "003": ("мм", "Миллиметр", False),
    "004": ("см", "Сантиметр", False),
    "005": ("дм", "Дециметр", False),
    "006": ("м", "Метр", False),
    "008": ("км", "Километр", False),
    "018": ("пог. м", "Погонный метр", False),
    # Длина (американские)
    "039": ("дюйм", "Дюйм (25,4 мм)", False),
    "041": ("фут", "Фут (0,3048 м)", False),
    "043": ("ярд", "Ярд (0,9144 м)", False),
    # Площадь
    "051": ("см²", "Квадратный сантиметр", False),
    "055": ("м²", "Квадратный метр", False),
    "059": ("га", "Гектар", False),
    "061": ("км²", "Квадратный километр", False),
    # Мощность и энергия
    "214": ("кВт", "Киловатт", False),
    "245": ("кВт·ч", "Киловатт-час", False),
    # Время
    "354": ("с", "Секунда", False),
    "355": ("мин", "Минута", False),
    "356": ("ч", "Час", False),
    "359": ("сут", "Сутки", False),
}


def validate_okei(okei_code: str) -> tuple[str, str, str, bool]:
    """Проверяет код ОКЕИ и возвращает (код ОКЕИ, обозначение, наименование, целое).

    Единицей измерения не может быть произвольное слово: допускаются только
    значения из классификатора.
    """
    value = (okei_code or "").strip()
    if value not in OKEI:
        raise AppError(
            "Единица измерения задаётся только кодом ОКЕИ из классификатора, "
            f"например 796 (штука). Значение «{okei_code}» в классификаторе отсутствует",
            rule="БП-01",
            field="okei_code",
        )
    code, name, is_integer = OKEI[value]
    return value, code, name, is_integer


# ---------------------------------------------------------------------------
# БП-02. Количество больше нуля; для штучных единиц — целое
# ---------------------------------------------------------------------------
def to_decimal(value: Any, field: str) -> Decimal:
    """Число из строки формы или из JSON; запятая допускается как разделитель."""
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, AttributeError, ValueError) as error:
        raise AppError(f"Поле «{field}»: требуется число", field=field) from error


def validate_quantity(value: Any, *, is_integer: bool, unit_code: str = "") -> Decimal:
    quantity = to_decimal(value, "quantity")
    if quantity <= 0:
        raise AppError("Количество должно быть больше нуля", rule="БП-02", field="quantity")
    if quantity.as_tuple().exponent < -3:  # type: ignore[operator]
        raise AppError(
            "Количество указывается не более чем с тремя знаками после запятой",
            rule="БП-02",
            field="quantity",
        )
    if is_integer and quantity != quantity.to_integral_value():
        raise AppError(
            f"Для единицы измерения «{unit_code}» количество должно быть целым",
            rule="БП-02",
            field="quantity",
        )
    return quantity


# ---------------------------------------------------------------------------
# БП-03. Цена не отрицательна; сумма строки = количество × цена
# ---------------------------------------------------------------------------
def validate_price(value: Any) -> Decimal:
    price = to_decimal(value, "price")
    if price < 0:
        raise AppError("Цена не может быть отрицательной", rule="БП-03", field="price")
    if price.as_tuple().exponent < -2:  # type: ignore[operator]
        raise AppError(
            "Цена указывается не более чем с двумя знаками после запятой",
            rule="БП-03",
            field="price",
        )
    return price


def amount(quantity: Decimal, price: Decimal) -> Decimal:
    """Сумма строки с округлением до копеек."""
    return (quantity * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# ---------------------------------------------------------------------------
# БП-04. Дата поступления не в будущем и не ранее 01.01.2000
# ---------------------------------------------------------------------------
def validate_receipt_date(value: date, *, today: date | None = None) -> date:
    current = today or date.today()
    if value > current:
        raise AppError(
            "Дата поступления не может быть позже сегодняшней",
            rule="БП-04",
            field="receipt_date",
        )
    if value < MIN_DATE:
        raise AppError(
            "Дата поступления не может быть ранее 01.01.2000", rule="БП-04", field="receipt_date"
        )
    return value


# ---------------------------------------------------------------------------
# БП-05. ИНН: 10 или 12 цифр с верной контрольной суммой
# ---------------------------------------------------------------------------
_INN10 = (2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN11 = (7, 2, 4, 10, 3, 5, 9, 4, 6, 8)
_INN12 = (3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8)


def _checksum(digits: list[int], weights: tuple[int, ...]) -> int:
    return sum(d * w for d, w in zip(digits, weights, strict=True)) % 11 % 10


def validate_inn(inn: str) -> str:
    value = (inn or "").strip()
    if not value.isdigit() or len(value) not in (10, 12):
        raise AppError(
            "ИНН должен содержать 10 цифр (организация) или 12 цифр (предприниматель)",
            rule="БП-05",
            field="inn",
        )
    digits = [int(char) for char in value]
    if len(digits) == 10:
        correct = _checksum(digits[:9], _INN10) == digits[9]
    else:
        correct = (
            _checksum(digits[:10], _INN11) == digits[10]
            and _checksum(digits[:11], _INN12) == digits[11]
        )
    if not correct:
        raise AppError("ИНН не проходит проверку контрольной суммы", rule="БП-05", field="inn")
    return value


# ---------------------------------------------------------------------------
# БП-06. Артикул материала: формат и уникальность
# ---------------------------------------------------------------------------
def validate_sku(sku: str) -> str:
    value = (sku or "").strip().upper()
    if not SKU_PATTERN.match(value):
        raise AppError(
            "Артикул содержит от 3 до 32 символов: заглавные латинские буквы, "
            "цифры и дефис (например, CEM-500)",
            rule="БП-06",
            field="sku",
        )
    return value


# ---------------------------------------------------------------------------
# БП-07. Материал не повторяется в одном поступлении
# ---------------------------------------------------------------------------
def ensure_material_not_repeated(used_material_ids: set[int], material_id: int) -> None:
    if material_id in used_material_ids:
        raise conflict(
            "Этот материал уже есть в документе — измените количество в существующей строке",
            rule="БП-07",
        )


# ---------------------------------------------------------------------------
# БП-09. Доступ по ролям
# ---------------------------------------------------------------------------
def require_admin(user: Any, operation: str) -> None:
    if getattr(user, "role", None) != "admin":
        raise forbidden(f"Операция «{operation}» доступна только администратору")


# ---------------------------------------------------------------------------
# БП-10. Пароль и логин
# ---------------------------------------------------------------------------
def validate_password(password: str, *, min_length: int = 8) -> str:
    if len(password or "") < min_length:
        raise AppError(
            f"Пароль должен содержать не менее {min_length} символов",
            rule="БП-10",
            field="password",
        )
    return password


def validate_login(login: str) -> str:
    value = (login or "").strip().lower()
    if not LOGIN_PATTERN.match(value):
        raise AppError(
            "Логин: от 3 до 50 символов — строчные латинские буквы, цифры, точка, "
            "дефис, знак подчёркивания",
            field="login",
        )
    return value


# ---------------------------------------------------------------------------
# Общие проверки текста
# ---------------------------------------------------------------------------
def validate_text(
    value: str | None,
    *,
    field: str,
    title: str,
    max_length: int,
    min_length: int = 2,
    required: bool = True,
) -> str | None:
    text = (value or "").strip()
    if not text:
        if required:
            raise AppError(f"Поле «{title}» обязательно для заполнения", field=field)
        return None
    if len(text) < min_length:
        raise AppError(f"Поле «{title}»: не менее {min_length} символов", field=field)
    if len(text) > max_length:
        raise AppError(f"Поле «{title}»: не более {max_length} символов", field=field)
    return text


# ---------------------------------------------------------------------------
# БП-08. Записи справочников со ссылками не удаляются
# ---------------------------------------------------------------------------
#: Ограничение базы данных -> понятное пользователю сообщение.
DB_ERRORS: dict[str, tuple[str, str | None]] = {
    "material_unit_id_fkey": ("Единица измерения используется материалами", "БП-08"),
    "receipt_supplier_id_fkey": ("Поставщик указан в поступлениях", "БП-08"),
    "receipt_item_material_id_fkey": ("Материал указан в поступлениях", "БП-08"),
    "receipt_created_by_fkey": ("Пользователь указан автором поступлений", "БП-08"),
    "unit_okei_code_key": ("Такая единица измерения уже есть в справочнике", "БП-01"),
    "unit_code_key": ("Такая единица измерения уже есть в справочнике", "БП-01"),
    "supplier_inn_key": ("Поставщик с таким ИНН уже существует", "БП-05"),
    "material_sku_key": ("Материал с таким артикулом уже существует", "БП-06"),
    "app_user_login_key": ("Пользователь с таким логином уже существует", None),
    "uq_receipt_item": ("Этот материал уже есть в документе", "БП-07"),
    "ck_unit_okei": ("Код ОКЕИ должен состоять из трёх цифр", "БП-01"),
    "ck_supplier_inn": ("ИНН должен содержать 10 или 12 цифр", "БП-05"),
    "ck_material_sku": ("Артикул не соответствует требуемому формату", "БП-06"),
    "ck_item_quantity": ("Количество должно быть больше нуля", "БП-02"),
    "ck_item_price": ("Цена не может быть отрицательной", "БП-03"),
    "ck_receipt_date": ("Дата поступления не может быть ранее 01.01.2000", "БП-04"),
    "ck_user_role": ("Указана недопустимая роль", "БП-09"),
    "ck_user_login": ("Логин не соответствует требуемому формату", None),
}


def database_error(constraint: str | None) -> AppError:
    """Ошибку целостности базы данных превращаем в понятное сообщение."""
    if constraint and constraint in DB_ERRORS:
        message, rule = DB_ERRORS[constraint]
        return conflict(message, rule=rule)
    return conflict("Операция нарушает ограничения целостности данных")
