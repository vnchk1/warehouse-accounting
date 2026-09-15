# Схема данных

Схема создаётся миграцией `migrations/versions/0001_initial.py`. Изменять схему
в обход миграций нельзя.

## Связи

```
unit ──1:N──▶ material ──1:N──▶ receipt_item ◀──N:1── receipt ──N:1──▶ supplier
                                                          │
                                                          └──N:1──▶ app_user (автор)
```

| Связь | Кратность | Удаление |
|---|---|---|
| `unit` → `material` | 1:N | `RESTRICT` — единицу измерения нельзя удалить, пока она используется |
| `material` → `receipt_item` | 1:N | `RESTRICT` |
| `supplier` → `receipt` | 1:N | `RESTRICT` |
| `app_user` → `receipt` | 1:N | `RESTRICT` |
| `receipt` → `receipt_item` | 1:N | `CASCADE` — строка не существует без поступления |

## Таблицы

### unit — единица измерения

| Поле | Тип | Ограничения |
|---|---|---|
| `id` | serial | первичный ключ |
| `code` | varchar(20) | уникален, например `кг` |
| `name` | varchar(100) | наименование из ОКЕИ |
| `okei_code` | varchar(3) | уникален, три цифры (`ck_unit_okei`) |
| `is_integer` | boolean | для штучных единиц количество должно быть целым |

### supplier — поставщик

| Поле | Тип | Ограничения |
|---|---|---|
| `id` | serial | первичный ключ |
| `name` | varchar(200) | наименование |
| `inn` | varchar(12) | уникален, 10 или 12 цифр (`ck_supplier_inn`); контрольная сумма проверяется приложением |
| `phone` | varchar(20) | необязательно |

### material — материал

| Поле | Тип | Ограничения |
|---|---|---|
| `id` | serial | первичный ключ |
| `sku` | varchar(32) | уникален, `^[A-Z0-9-]{3,32}$` (`ck_material_sku`) |
| `name` | varchar(200) | наименование |
| `unit_id` | integer | ссылка на `unit` |

### app_user — пользователь

| Поле | Тип | Ограничения |
|---|---|---|
| `id` | serial | первичный ключ |
| `login` | varchar(50) | уникален, `^[a-z0-9_.-]{3,50}$` |
| `password_hash` | varchar(255) | argon2id; открытый пароль не хранится |
| `full_name` | varchar(150) | ФИО |
| `role` | varchar(20) | `admin` или `storekeeper` (`ck_user_role`) |

### receipt — поступление

| Поле | Тип | Ограничения |
|---|---|---|
| `id` | serial | первичный ключ, он же номер документа |
| `receipt_date` | date | не ранее 01.01.2000 (`ck_receipt_date`); «не в будущем» проверяет приложение |
| `supplier_id` | integer | ссылка на `supplier` |
| `created_by` | integer | ссылка на `app_user` |
| `created_at` | timestamptz | момент создания |

### receipt_item — строка поступления

| Поле | Тип | Ограничения |
|---|---|---|
| `id` | serial | первичный ключ |
| `receipt_id` | integer | ссылка на `receipt`, каскадное удаление |
| `material_id` | integer | ссылка на `material` |
| `quantity` | numeric(12,3) | `> 0` (`ck_item_quantity`) |
| `price` | numeric(12,2) | `>= 0` (`ck_item_price`) |
| `amount` | numeric(14,2) | вычисляется базой: `round(quantity * price, 2)` |
| — | — | пара (`receipt_id`, `material_id`) уникальна (`uq_receipt_item`) |

## Индексы

`ix_material_unit`, `ix_receipt_date`, `ix_receipt_supplier`, `ix_item_receipt`,
`ix_item_material` — для отбора по периоду, поставщику и соединения строк.

## Правила и где они проверяются

| Правило | Приложение | База данных |
|---|---|---|
| БП-01 ЕИ только из ОКЕИ | `rules.validate_okei` | `ck_unit_okei`, уникальность `okei_code` |
| БП-02 количество > 0, для штучных — целое | `rules.validate_quantity` | `ck_item_quantity` |
| БП-03 цена ≥ 0, сумма = количество × цена | `rules.validate_price` | `ck_item_price`, вычисляемое поле `amount` |
| БП-04 дата не в будущем, не ранее 2000 | `rules.validate_receipt_date` | `ck_receipt_date` |
| БП-05 ИНН 10/12 цифр с контрольной суммой | `rules.validate_inn` | `ck_supplier_inn`, уникальность |
| БП-06 артикул уникален и по формату | `rules.validate_sku` | `ck_material_sku`, уникальность |
| БП-07 материал не повторяется в поступлении | `rules.ensure_material_not_repeated` | `uq_receipt_item` |
| БП-08 запись со ссылками не удаляется | `rules.database_error` | внешние ключи `ON DELETE RESTRICT` |
| БП-09 доступ по ролям | `rules.require_admin` | `ck_user_role` |
| БП-10 пароль ≥ 8 символов, хранится хеш | `rules.validate_password`, `auth.hash_password` | — |

## Миграции

```bash
make migrate                      # применить
.venv/bin/alembic downgrade -1    # откатить последнюю
.venv/bin/alembic revision -m "…" # создать новую
```
