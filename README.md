# Pathformer Microservices

Два Python-микросервиса для планирования маршрута беспилотного надводного судна (USV):

- **map-service** — генерирует синтетические морские карты (суша + поля течений)
- **planner-service** — строит траекторию через нейросеть (`best.pt`) и чинит коллизии с сушей через A*

Сервисы хранят артефакты в **MinIO** (S3-совместимое), события публикуют в **RabbitMQ**.

---

## Структура проекта

```
Pathformer/
├── docker-compose.yml             # 4 контейнера: 2 сервиса + minio + rabbitmq
├── .env.example                   # шаблон переменных окружения
├── shared/
│   └── events.py                  # Pydantic-схемы событий (MapCreated, PlanCompleted)
├── docs/
│   ├── rabbitmq.md                # теория по RabbitMQ
│   └── s3-session-client.md       # теория по S3 / aioboto3
│
├── map-service/                   # Python 3.11
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/
│       ├── main.py                # FastAPI lifespan — composition root
│       ├── unit_of_work.py        # буферизует upload + publish, атомарный commit
│       ├── adapters/
│       │   ├── storage.py         # S3Client (aioboto3) + ABC
│       │   └── broker.py          # RabbitBroker (aio-pika) + ABC
│       ├── api/
│       │   └── routes.py          # POST /generate, GET /health
│       ├── configs/
│       │   ├── domain.py          # max_current_global
│       │   ├── minio.py           # MINIO_*
│       │   └── rabbitmq.py        # RABBITMQ_*
│       ├── core/
│       │   ├── generator.py       # обёртка над generate_map
│       │   └── generate_map/      # сам алгоритм: шум Перлина → суша + течения
│       ├── domain/
│       │   └── service.py         # GenerateMapUseCase
│       └── schemas/
│           └── generate.py        # GenerateRequest, GenerateResponse
│
├── planner-service/               # Python 3.12 (torch CPU)
│   ├── Dockerfile
│   ├── requirements.txt
│   └── src/
│       ├── main.py
│       ├── unit_of_work.py        # buffer upload + publish, прямой passthrough для download
│       ├── adapters/
│       │   ├── storage.py         # S3Client с download
│       │   └── broker.py
│       ├── api/
│       │   └── routes.py          # POST /plan, GET /plans/{id}/image, GET /health
│       ├── configs/
│       │   ├── domain.py          # weights_path, max_current_global
│       │   ├── minio.py
│       │   └── rabbitmq.py
│       ├── core/
│       │   ├── planner.py         # ModelPlanner — load best.pt + inference
│       │   ├── preprocessor.py    # raw .npz → 3-канальный тензор (u, v, safety)
│       │   ├── visualizer.py      # render PNG через matplotlib
│       │   └── pathformer/        # нейросеть (CNN энкодер + Transformer декодер)
│       ├── domain/
│       │   └── service.py         # PlanPathUseCase
│       └── schemas/
│           └── plan.py            # PlanRequest, PlanResponse
│
└── weights/
    └── best.pt                    # предобученные веса (монтируются в planner)
```

### Зачем `unit_of_work.py`

UoW буферизует все сторонние эффекты (upload в S3, publish в RabbitMQ) внутри одного `async with`. На выходе из блока:

- если ошибки нет → `commit()` накатывает всё разом
- если упало → `rollback()` отбрасывает буферы

Это даёт ближе к атомарности, чем "загрузил в S3, упал на publish" — событие не уйдёт, если upload не прошёл, и наоборот.

В planner-service `download` не буферится — он read-only и passthrough'ом идёт в `S3Client` напрямую.

---

## Быстрый старт

### 1. Скопировать конфиг

```bash
cp .env.example .env
```

### 2. Положить веса модели

```bash
mkdir -p weights
# Скопируй best.pt в weights/best.pt
```

### 3. Запустить

```bash
docker compose up --build
```

Дождись `Application startup complete` от обоих сервисов.

---

## Использование

### 1. Сгенерировать карту

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"height": 128, "width": 128, "seed": 42}'
```

Ответ:
```json
{"map_id": "550e8400-e29b-41d4-a716-446655440000", "grid_size": [128, 128]}
```

Карта сохраняется в MinIO (`maps/<map_id>.npz`), событие `map.created` публикуется в RabbitMQ.

### 2. Построить маршрут

```bash
curl -X POST http://localhost:8001/plan \
  -H "Content-Type: application/json" \
  -d '{
    "map_id": "<uuid>",
    "start": [10, 10],
    "goal": [110, 110],
    "vessel_max_current": 2.0
  }'
```

Ответ:
```json
{
  "plan_id": "...",
  "waypoints": [[10, 10], [12, 14], ...],
  "success": true,
  "n_repairs": 0,
  "visual_url": "/plans/<plan_id>/image"
}
```

### 3. Скачать визуализацию

```
http://localhost:8001/plans/<plan_id>/image
```

```bash
curl -o path.png "http://localhost:8001/plans/<plan_id>/image"
```

### Swagger UI

- map-service: http://localhost:8000/docs
- planner-service: http://localhost:8001/docs

---

## API

### `POST /generate` — map-service

| Поле | Тип | По умолчанию | Описание |
|------|-----|---|---|
| `height` | int | 128 | Высота (16–1024) |
| `width` | int | 128 | Ширина (16–1024) |
| `seed` | int \| null | null | Для воспроизводимости |

⚠️ Для `planner-service` карта должна быть **≤ 256×256** — модель училась на этом размере.

### `POST /plan` — planner-service

| Поле | Тип | По умолчанию | Описание |
|------|-----|---|---|
| `map_id` | str | — | UUID карты из `/generate` |
| `start` | `[int, int]` | — | Стартовая точка `[row, col]` в пикселях |
| `goal` | `[int, int]` | — | Целевая точка `[row, col]` в пикселях |
| `vessel_max_current` | float | 1.0 | Сила судна (м/с): `0.5` — лёгкий, `1.0` — средний, `2.0–3.0` — тяжёлый |

Поля ответа:
- `success` — достиг ли путь цели в пределах `goal_threshold`
- `n_repairs` — сколько сегментов заменено A* (коллизии с сушей)
- `waypoints` — список `[row, col]` пиксельных координат

### `GET /plans/{plan_id}/image`

Возвращает PNG с визуализацией пути на фоне карты.

### `GET /health`

Базовый healthcheck — оба сервиса возвращают `{"status": "ok"}`.

---

## Архитектура

```
            ┌────────────┐
Клиент ────▶│ /generate  │
            ├─map-service┤────upload .npz────▶ MinIO (maps/)
            │            │────publish map.created──▶ RabbitMQ
            └────────────┘

            ┌────────────┐
Клиент ────▶│   /plan    │
            ├planner-svc │◀───download .npz───── MinIO (maps/)
            │            │────upload .png─────▶ MinIO (visuals/)
            │            │────publish plan.completed──▶ RabbitMQ
            └────────────┘

Клиент ──GET /plans/{id}/image──▶ planner-service ──stream PNG──▶ Клиент
```

### Стек

| Слой | Технология |
|---|---|
| Web | FastAPI + Uvicorn |
| ML | PyTorch (CPU only в Docker) |
| Storage | MinIO (S3 API) через `aioboto3` |
| Broker | RabbitMQ через `aio-pika` |
| Validation | Pydantic v2 + pydantic-settings |
| Numerics | NumPy, SciPy, scikit-image |

### Нейросеть `best.pt`

- **Энкодер**: 5-слойный CNN, сжимает карту (3 канала × H × W) в токены размерности 256
- **Декодер**: 4-слойный Transformer, авторегрессивно предсказывает смещения `(Δrow, Δcol)` шаг за шагом
- **A\* repair**: каждый сегмент пути, который пересекает сушу, локально перепланируется через A\*

Препроцессинг карт идентичен каноническому pathformer:

| Канал | Формула |
|---|---|
| `u` | `intensity * cos(direction) / max_current_global` |
| `v` | `intensity * sin(direction) / max_current_global` |
| `safety_field` | `1 - clip(distance_transform_edt(1 - land_mask) / 20, 0, 1)` |

### Layered архитектура

В обоих сервисах одна структура (Clean Architecture без догматичных Protocol-портов):

```
schemas/   — DTO для HTTP (Pydantic request/response)
api/       — FastAPI routes
domain/    — use-case (бизнес-логика)
core/      — внутренние утилиты (генератор, нейросеть, рендер)
adapters/  — внешние интеграции (S3, RabbitMQ) — с ABC-интерфейсами
configs/   — pydantic-settings, по одному файлу на источник конфига
unit_of_work.py — буфер upload + publish для атомарного commit
main.py    — composition root: создаёт адаптеры, собирает use-case
```

---

## Состояние очереди

⚠️ **На текущий момент** оба сервиса **публикуют** события в RabbitMQ, но **никто их не потребляет**. Сообщения летят в exchange `pathformer` (topic, durable) и пропадают, потому что нет привязанных queue.

Это сделано как заготовка под:
- consumer для записи событий в БД (audit log)
- автоматический запуск `/plan` по `map.created`
- WebSocket-нотификации UI

Подробности про устройство RabbitMQ — в [docs/rabbitmq.md](docs/rabbitmq.md).

---

## Инфраструктура

| Сервис | URL | Логин |
|---|---|---|
| map-service API | http://localhost:8000 | — |
| map-service Swagger | http://localhost:8000/docs | — |
| planner-service API | http://localhost:8001 | — |
| planner-service Swagger | http://localhost:8001/docs | — |
| MinIO Console | http://localhost:9001 | `minioadmin` / `minioadmin` |
| RabbitMQ Management | http://localhost:15672 | `guest` / `guest` |

---

## Отладка

```bash
# Логи
docker compose logs -f map-service
docker compose logs -f planner-service

# Перезапустить только один сервис (быстрее, чем полная пересборка)
docker compose up -d --build planner-service

# Остановить, сохранить данные MinIO/RabbitMQ
docker compose down

# Полная очистка с volumes
docker compose down -v
```

### Типичные проблемы

| Симптом | Причина |
|---|---|
| `ModuleNotFoundError: No module named 'pathformer'` при `/plan` | Веса `best.pt` сериализованы с путём `pathformer.*` — в `src/core/pathformer/__init__.py` зарегистрированы алиасы в `sys.modules`, не удаляй их |
| `SignatureDoesNotMatch` при upload в MinIO | Не передан `signature_version="s3v4"` в boto config — у нас уже есть в `adapters/storage.py` |
| `/plan` возвращает 500 | Смотри `docker compose logs planner-service` — `logger.exception("plan failed")` пишет полный traceback |
| `path failed` на картинке (красный путь) | Не баг кода — модель не справилась с картой. Попробуй `vessel_max_current: 2.0` или другую `seed` |
| Карта > 256×256 → `ValueError` | Pretrained PE капается на 16×16 токенов (16x downsample), модель училась максимум на 256×256 |

---

## Документация

- [docs/rabbitmq.md](docs/rabbitmq.md) — теория RabbitMQ (exchange, queue, binding, ack, ...)
- [docs/s3-session-client.md](docs/s3-session-client.md) — устройство `aioboto3` (Session, Client, connection pool)
- [docs/PROJECT_OVERVIEW.md](docs/PROJECT_OVERVIEW.md) — обзор проекта
- [docs/PLAN.md](docs/PLAN.md) — план развития
- [docs/REPORT.md](docs/REPORT.md) — отчёт по архитектуре
