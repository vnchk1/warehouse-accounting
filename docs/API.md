# HTTP API

Префикс `/api/v1`, формат обмена — JSON (UTF-8). Интерактивное описание —
`/docs`, машиночитаемое — `/openapi.json`.

Аутентификация сеансовая: `POST /api/v1/auth/login` устанавливает файл cookie
`warehouse_session` (HttpOnly, SameSite=Lax), который клиент передаёт в
последующих запросах. Все методы, кроме `/healthz`, требуют входа.

Количества, цены и суммы передаются строками («10.500»), чтобы не терять
точность. Даты — в формате `ГГГГ-ММ-ДД`. Денежные величины (`price`, `amount`,
`total`) выражены в рублях; в экранных формах это указано в заголовках
столбцов «Цена (руб.)» и «Сумма (руб.)».

## Формат ошибки

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Количество должно быть больше нуля",
    "rule": "БП-02",
    "field": "quantity"
  }
}
```

| Ситуация | Код | `code` |
|---|---|---|
| Неверный формат или состав запроса | 422 | `BAD_REQUEST` |
| Нарушено правило предметной области | 422 | `VALIDATION_ERROR` |
| Не выполнен вход, неверный пароль | 401 | `UNAUTHORIZED` |
| Операция недоступна роли | 403 | `FORBIDDEN` |
| Объект не найден | 404 | `NOT_FOUND` |
| Нарушение уникальности или ссылки | 409 | `CONFLICT` |
| Непредвиденная ошибка | 500 | `INTERNAL_ERROR` |

## Методы

### Служебные

| Метод | Назначение | Ответы |
|---|---|---|
| `GET /healthz` | Проверка работоспособности и доступности БД | 200, 503 |

### Аутентификация

| Метод | Назначение | Ответы |
|---|---|---|
| `POST /api/v1/auth/login` | Вход: `{"login": "...", "password": "..."}` | 200, 401 |
| `POST /api/v1/auth/logout` | Выход | 204, 401 |
| `GET /api/v1/auth/me` | Текущий пользователь | 200, 401 |

### Единицы измерения (изменение — только администратор)

| Метод | Назначение | Ответы |
|---|---|---|
| `GET /api/v1/units` | Справочник | 200 |
| `GET /api/v1/units/okei` | Допустимые коды ОКЕИ | 200 |
| `POST /api/v1/units` | Добавить: `{"okei_code": "796"}` | 201, 403, 409, 422 |
| `DELETE /api/v1/units/{id}` | Удалить | 204, 403, 404, 409 |

### Поставщики

| Метод | Назначение | Ответы |
|---|---|---|
| `GET /api/v1/suppliers` | Перечень | 200 |
| `POST /api/v1/suppliers` | Создать: `{"name", "inn", "phone"}` | 201, 409, 422 |
| `PUT /api/v1/suppliers/{id}` | Изменить | 200, 404, 409, 422 |
| `DELETE /api/v1/suppliers/{id}` | Удалить | 204, 404, 409 |

### Материалы

| Метод | Назначение | Ответы |
|---|---|---|
| `GET /api/v1/materials` | Перечень | 200 |
| `POST /api/v1/materials` | Создать: `{"sku", "name", "unit_id"}` | 201, 404, 409, 422 |
| `PUT /api/v1/materials/{id}` | Изменить | 200, 404, 409, 422 |
| `DELETE /api/v1/materials/{id}` | Удалить | 204, 404, 409 |

### Поступления

| Метод | Назначение | Ответы |
|---|---|---|
| `GET /api/v1/receipts` | Перечень; отбор `date_from`, `date_to`, `supplier_id` | 200 |
| `POST /api/v1/receipts` | Создать: `{"receipt_date", "supplier_id"}` | 201, 404, 422 |
| `GET /api/v1/receipts/{id}` | Поступление со строками | 200, 404 |
| `DELETE /api/v1/receipts/{id}` | Удалить вместе со строками | 204, 404 |
| `POST /api/v1/receipts/{id}/items` | Строка: `{"material_id", "quantity", "price"}` | 201, 404, 409, 422 |
| `DELETE /api/v1/receipts/{id}/items/{item_id}` | Удалить строку | 204, 404 |

### Отчёт и пользователи

| Метод | Назначение | Ответы |
|---|---|---|
| `GET /api/v1/reports/receipts` | Свод по поставщикам; отбор `date_from`, `date_to`, `supplier_id` | 200, 422 |
| `GET /api/v1/users` | Учётные записи (администратор) | 200, 403 |
| `POST /api/v1/users` | Создать: `{"login", "full_name", "role", "password"}` | 201, 403, 409, 422 |
| `DELETE /api/v1/users/{id}` | Удалить | 204, 403, 404, 409 |

## Пример работы

```bash
BASE=http://localhost:8000/api/v1

curl -s -c cookies.txt -X POST $BASE/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"login":"admin","password":"..."}'

curl -s -b cookies.txt -X POST $BASE/units -H 'Content-Type: application/json' \
  -d '{"okei_code":"166"}'

curl -s -b cookies.txt -X POST $BASE/materials -H 'Content-Type: application/json' \
  -d '{"sku":"CEM-500","name":"Цемент М500","unit_id":1}'

curl -s -b cookies.txt -X POST $BASE/receipts -H 'Content-Type: application/json' \
  -d '{"receipt_date":"2026-09-13","supplier_id":1}'

curl -s -b cookies.txt -X POST $BASE/receipts/1/items -H 'Content-Type: application/json' \
  -d '{"material_id":1,"quantity":"10.5","price":"345.90"}'

curl -s -b cookies.txt "$BASE/reports/receipts?date_from=2026-09-01&date_to=2026-09-30"
```

Пример отказа по правилу предметной области:

```bash
curl -s -b cookies.txt -X POST $BASE/units -H 'Content-Type: application/json' \
  -d '{"okei_code":"мешок"}'
# 422
# {"error":{"code":"VALIDATION_ERROR","rule":"БП-01","field":"okei_code",
#   "message":"Единица измерения задаётся только кодом ОКЕИ из классификатора…"}}
```
