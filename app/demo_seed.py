"""Наполнение базы данных демонстрационными данными.

Запуск: make demo_seed. Добавляет поставщиков, материалы и поступления, чтобы
приложение можно было показать на заполненной базе, не вводя данные вручную.

Сценарий безопасен для повторного запуска: поставщики и материалы добавляются
только те, которых ещё нет (сверка по ИНН и артикулу), а поступления
создаются один раз — если в базе уже есть хотя бы одно поступление, этот шаг
пропускается. Данные детерминированы: один и тот же запуск даёт один и тот же
результат.
"""

from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app import db, rules, services
from app.config import get_settings
from app.models import Material, Receipt, Supplier, User

# ---------------------------------------------------------------------------
# Демонстрационные данные
# ---------------------------------------------------------------------------
#: Поставщики: наименование, ИНН (контрольная сумма верна), телефон.
SUPPLIERS: list[tuple[str, str, str]] = [
    ("ООО «Стройресурс»", "7701012342", "+7 495 120-14-70"),
    ("АО «Северный цемент»", "7812034567", "+7 812 305-88-21"),
    ("ООО «Метизы и крепёж»", "5027043219", "+7 498 617-30-045"),
    ("ООО «ЮгМеталлТорг»", "6165056782", "+7 863 244-19-52"),
    ("АО «Сибирский лес»", "5402067893", "+7 383 388-71-16"),
    ("ООО «Кубань-Отделка»", "2303076549", "+7 861 210-55-08"),
    ("ООО «Урал Инструмент»", "6674089126", "+7 343 379-62-40"),
    ("ООО «Балтийская химия»", "7815090016", "+7 812 449-07-33"),
    ("ИП Ковалёв А. С.", "771234567859", "+7 916 402-18-77"),
    ("ИП Насибуллина Р. М.", "616123450973", "+7 918 771-26-04"),
]

#: Материалы: артикул, наименование, код ОКЕИ (см. app/rules.py).
MATERIALS: list[tuple[str, str, str]] = [
    ("CEM-500", "Цемент портландский М500 Д0", "168"),  # тонна
    ("CEM-400", "Цемент портландский М400 Д20", "168"),  # тонна
    ("SAND-KR", "Песок карьерный крупнозернистый", "113"),  # м³
    ("SHEB-20", "Щебень гранитный фракция 20-40", "113"),  # м³
    ("KIR-RED", "Кирпич керамический рядовой М150", "796"),  # штука
    ("KIR-OBL", "Кирпич облицовочный красный М200", "796"),  # штука
    ("BLOK-GAZ", "Блок газобетонный D500 600x300x200", "796"),  # штука
    ("DOSK-5010", "Доска обрезная хвойная 50x100", "113"),  # м³
    ("BRUS-100", "Брус строительный 100x100", "113"),  # м³
    ("FANER-12", "Фанера ФК 1525x1525x12", "055"),  # м²
    ("GKL-125", "Гипсокартон стеновой 2500x1200x12.5", "055"),  # м²
    ("UTEP-MIN", "Утеплитель минераловатный 100 мм", "055"),  # м²
    ("ARM-A500", "Арматура А500С диаметр 12 мм", "168"),  # тонна
    ("PROF-40", "Профиль потолочный 60x27", "006"),  # метр
    ("TRUBA-PP", "Труба полипропиленовая PN20 диаметр 25", "006"),  # метр
    ("KABEL-3X25", "Кабель ВВГнг 3x2.5", "006"),  # метр
    ("GVOZD-100", "Гвозди строительные 4.0x100", "166"),  # килограмм
    ("SAMOR-35", "Саморезы по дереву 3.5x35", "166"),  # килограмм
    ("ANKER-M10", "Анкер клиновой М10x100", "796"),  # штука
    ("GRUNT-10", "Грунтовка глубокого проникновения", "112"),  # литр
    ("KRASKA-AK", "Краска акриловая фасадная белая", "112"),  # литр
    ("LAK-PU", "Лак полиуретановый паркетный", "112"),  # литр
    ("KLEY-PL", "Клей для плитки усиленный", "166"),  # килограмм
    ("SHPAT-FIN", "Шпатлёвка финишная полимерная", "166"),  # килограмм
    ("PERCH-SET", "Перчатки рабочие с ПВХ-покрытием", "715"),  # пара
    ("OTVERT-SET", "Набор отвёрток диэлектрических", "839"),  # комплект
]

#: Поступления: сколько создать и за какой период.
RECEIPT_COUNT = 28
PERIOD_DAYS = 180
#: Цена за единицу: минимум и максимум, в рублях.
PRICE_RANGE: dict[str, tuple[int, int]] = {
    "168": (4_200, 9_800),
    "113": (1_100, 8_500),
    "796": (14, 120),
    "055": (180, 950),
    "006": (38, 640),
    "166": (95, 480),
    "112": (210, 1_450),
    "715": (45, 130),
    "839": (890, 2_400),
}


def _quantity(random_source: random.Random, okei_code: str) -> Decimal:
    """Правдоподобное количество с учётом того, штучная ли единица."""
    is_integer = rules.OKEI[okei_code][2]
    if is_integer:
        return Decimal(random_source.choice([4, 6, 10, 12, 20, 24, 50, 100, 250, 500, 1000]))
    return Decimal(str(random_source.randrange(500, 60_000) / 100))


def _price(random_source: random.Random, okei_code: str) -> Decimal:
    low, high = PRICE_RANGE.get(okei_code, (50, 1_500))
    return Decimal(str(random_source.randrange(low * 100, high * 100) / 100)).quantize(
        Decimal("0.01")
    )


def main() -> int:
    settings = get_settings()
    db.setup()
    session = next(db.get_session())
    random_source = random.Random(20260913)  # одинаковый результат при каждом запуске

    admin = session.execute(
        select(User).where(User.login == settings.admin_login.strip().lower())
    ).scalar_one_or_none()
    if admin is None:
        print("Администратор не найден. Сначала выполните: make seed")  # noqa: T201
        return 1

    # -- Поставщики ---------------------------------------------------------
    added_suppliers = 0
    suppliers: list[Supplier] = []
    for name, inn, phone in SUPPLIERS:
        existing = session.execute(select(Supplier).where(Supplier.inn == inn)).scalar_one_or_none()
        if existing is None:
            existing = services.save_supplier(session, name=name, inn=inn, phone=phone)
            added_suppliers += 1
        suppliers.append(existing)
    print(f"Поставщики: добавлено {added_suppliers}, всего {len(suppliers)}")  # noqa: T201

    # -- Материалы ----------------------------------------------------------
    added_materials = 0
    materials: list[tuple[Material, str]] = []
    for sku, name, okei_code in MATERIALS:
        existing = session.execute(select(Material).where(Material.sku == sku)).scalar_one_or_none()
        if existing is None:
            existing = services.save_material(session, sku=sku, name=name, okei_code=okei_code)
            added_materials += 1
        materials.append((existing, okei_code))
    print(f"Материалы: добавлено {added_materials}, всего {len(materials)}")  # noqa: T201

    session.commit()

    # -- Поступления --------------------------------------------------------
    already = session.execute(select(func.count()).select_from(Receipt)).scalar_one()
    if already:
        print(  # noqa: T201
            f"Поступления: в базе уже есть {already} — повторно не добавляются.\n"
            "Чтобы наполнить базу заново, очистите её: make migrate && make seed"
        )
        session.close()
        return 0

    today = date.today()
    lines_total = 0
    for number in range(RECEIPT_COUNT):
        receipt_date = today - timedelta(days=random_source.randrange(0, PERIOD_DAYS))
        supplier = suppliers[number % len(suppliers)]
        receipt = services.create_receipt(
            session, admin, receipt_date=receipt_date, supplier_id=supplier.id
        )
        # 2–5 разных материалов в документе: повторы в одном документе запрещены
        for material, okei_code in random_source.sample(materials, random_source.randrange(2, 6)):
            services.add_item(
                session,
                receipt_id=receipt.id,
                material_id=material.id,
                quantity=_quantity(random_source, okei_code),
                price=_price(random_source, okei_code),
            )
            lines_total += 1

    session.commit()
    total = session.execute(select(func.count()).select_from(Receipt)).scalar_one()
    print(f"Поступления: добавлено {total}, строк в них {lines_total}")  # noqa: T201
    print("Демонстрационные данные готовы. Откройте раздел «Отчёт».")  # noqa: T201
    session.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except rules.AppError as error:  # понятное сообщение вместо трассировки
        print(f"Не удалось наполнить базу: {error.message}")  # noqa: T201
        sys.exit(1)
