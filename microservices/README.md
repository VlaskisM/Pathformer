# Pathformer Microservices

Два Python-микросервиса вокруг pathformer: генерация синтетических морских карт и планирование траектории для беспилотного судна.

## Состав

- **map-service** (порт 8000) — генерирует карту (суша + течения), сохраняет `.npz` в MinIO, публикует событие `map.created` в RabbitMQ.
- **planner-service** (порт 8001) — по `map_id` строит траекторию через загруженные веса `best.pt`, рендерит PNG-визуализацию, публикует `plan.completed`.
- **rabbitmq** — топик-exchange `pathformer` (UI: http://localhost:15672, guest/guest).
- **minio** — S3-хранилище для карт (`maps/`) и визуализаций (`visuals/`). UI: http://localhost:9001 (minioadmin/minioadmin).

## Структура репозитория

```
Project_net_vuz/
├── pathformer/        ← старый проект (исходники pathformer, best.pt)
└── microservices/     ← эта папка — вся новая программа
    ├── docker-compose.yml
    ├── map-service/
    ├── planner-service/
    └── shared/
```

## Запуск

Запускать из папки `microservices/`:

```bash
cd microservices
docker compose up --build
```

Сборка использует `context: ..` и видит родительскую папку, чтобы скопировать `pathformer/synthetic_generator/` в образы и смонтировать `../pathformer/best.pt` в планнер.

Подождать пока все 4 контейнера перейдут в `healthy`.

## Использование

### 1. Сгенерировать карту

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"height":128,"width":128,"seed":42}'
# {"map_id":"<uuid>","grid_size":[128,128]}
```

### 2. Построить путь

```bash
curl -X POST http://localhost:8001/plan \
  -H "Content-Type: application/json" \
  -d '{"map_id":"<uuid>","start":[10,10],"goal":[120,120],"vessel_max_current":1.0}'
# {"plan_id":"<uuid>","waypoints":[[r,c],...],"success":true,"n_repairs":2,"visual_url":"/plans/<uuid>/image"}
```

### 3. Посмотреть график

Открыть в браузере:
```
http://localhost:8001/plans/<plan_id>/image
```

Или сохранить в файл:
```bash
curl -o path.png http://localhost:8001/plans/<plan_id>/image
```

**Поля ответа `POST /plan`:**
- `plan_id` — uuid запроса
- `waypoints` — точки пути `(row, col)` в пикселях
- `success` — дошёл ли путь до цели (нейросеть объявила успех)
- `n_repairs` — сколько сегментов пути пришлось переложить через A* (обход суши). 0 — нейросеть не цеплялась за берег; >0 — отдельные участки были заменены локальными обходами
- `visual_url` — относительный URL для `GET /plans/{id}/image`

## Параметры

### `POST /generate`
| Поле | Тип | Дефолт | Описание |
|------|-----|--------|----------|
| `height` | int | 128 | Высота карты в пикселях |
| `width` | int | 128 | Ширина карты |
| `seed` | int | random | Seed генерации (для воспроизводимости) |

### `POST /plan`
| Поле | Тип | Дефолт | Описание |
|------|-----|--------|----------|
| `map_id` | str | — | UUID карты, полученный от `/generate` |
| `start` | [int, int] | — | Стартовая точка `(row, col)` в пикселях |
| `goal` | [int, int] | — | Цель `(row, col)` в пикселях |
| `vessel_max_current` | float | 1.0 | Max сила течения (м/с), которую судно может преодолеть. Значения из обучения pathformer: `0.5` light_usv, `1.0` medium_usv, `2.0` heavy_usv. Внутри нормализуется делением на `max_current_global=3.0`. |

## Архитектура

```
Client ──HTTP──> map-service ──publish──> RabbitMQ (map.created)
                     │
                     └─ upload .npz ──> MinIO (maps/)

Client ──HTTP──> planner-service ──download──> MinIO (maps/)
                     │            ──upload PNG──> MinIO (visuals/)
                     └─ publish ──> RabbitMQ (plan.completed)
Client ──HTTP──> planner-service/plans/{id}/image
```

Веса `best.pt` монтируются из `pathformer/best.pt` в планнер-контейнер на `/app/weights/best.pt` (read-only).

## SOLID

В обоих сервисах один каркас:

```
app/
├── api/        # HTTP routes (FastAPI)
├── core/       # config, generator/planner, preprocessing, visualizer
├── adapters/   # S3 (boto3), RabbitMQ (aio-pika) — конкретные реализации
└── domain/
    ├── ports.py     # Protocol-интерфейсы (StoragePort, BrokerPort, ...)
    └── service.py   # Use-case, зависит ТОЛЬКО от портов (DIP)
main.py         # Composition root: создаёт адаптеры, инжектит в use-case
```

Use-case `domain/service.py` не импортирует ни boto3, ни aio-pika. Это делает его unit-тестируемым без инфраструктуры.

## Отладка

**Логи сервисов:**
```bash
docker compose logs -f map-service
docker compose logs -f planner-service
```

**MinIO UI:** http://localhost:9001 (minioadmin/minioadmin) — просмотр бакетов `maps/` и `visuals/`.

**RabbitMQ UI:** http://localhost:15672 (guest/guest) — в разделе Exchanges видно `pathformer` и счётчики публикаций.

## Остановка

```bash
docker compose down         # сохранить volume с данными MinIO
docker compose down -v      # + удалить данные
```
