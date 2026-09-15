"""Начальное наполнение базы данных: справочник ОКЕИ и администратор.

Запуск: make seed. Повторный запуск ничего не дублирует.
"""

from __future__ import annotations

from sqlalchemy import select

from app import db, rules
from app.auth import hash_password
from app.config import get_settings
from app.models import Unit, User


def main() -> None:
    settings = get_settings()
    db.setup()
    session = next(db.get_session())

    # Обозначение и код ОКЕИ уникальны каждый по отдельности, поэтому
    # пропускаем запись, если занято хотя бы одно из двух значений: иначе
    # повторный запуск на непустой базе завершился бы отказом.
    existing = session.execute(select(Unit.code, Unit.okei_code)).all()
    codes = {row[0] for row in existing}
    okei_codes = {row[1] for row in existing}
    added = 0
    for okei_code, (code, name, is_integer) in sorted(rules.OKEI.items()):
        if code in codes or okei_code in okei_codes:
            continue
        session.add(Unit(code=code, name=name, okei_code=okei_code, is_integer=is_integer))
        codes.add(code)
        okei_codes.add(okei_code)
        added += 1
    print(f"Единицы измерения: добавлено {added}, всего {len(codes)}")  # noqa: T201

    login = settings.admin_login.strip().lower()
    if session.execute(select(User).where(User.login == login)).scalar_one_or_none():
        print(f"Администратор «{login}» уже существует")  # noqa: T201
    else:
        session.add(
            User(
                login=login,
                password_hash=hash_password(settings.admin_password),
                full_name="Администратор",
                role="admin",
            )
        )
        print(f"Создан администратор «{login}» с паролем из ADMIN_PASSWORD")  # noqa: T201

    session.commit()
    session.close()


if __name__ == "__main__":
    main()
