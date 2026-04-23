# Pathformer Microservices

Два Python-микросервиса для планирования маршрута беспилотного надводного судна (USV):

- **map-service** — генерирует синтетические морские карты (суша + течения)
- **planner-service** — строит траекторию с помощью нейросети (`best.pt`) и чинит коллизии через A*

Сервисы общаются через RabbitMQ, хранят артефакты в MinIO (S3-совместимое хранилище).

---

## Структура

```
Pathformer/
├── docker-compose.yml         # оркестрация 4 контейнеров
├── .env.example               # шаблон переменных окружения
├── shared/                    # общие Pydantic-схемы событий
│   └── events.py              # MapCreated, PlanCompleted
├── map-service/               # сервис генерации карт (Python 3.11)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── generate_map/          # алгоритм: шум Перлина → суша + течения → A*
│   └── app/                   # FastAPI + Clean Architecture
│       ├── api/               # HTTP-маршруты
│       ├── core/              # конфиг, обёртка генератора
│       ├── domain/            # use-case + Protocol-порты
│       └── adapters/          # S3 (boto3), RabbitMQ (aio-pika)
├── planner-service/           # сервис планирования пути (Python 3.12)
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── pathformer/            # нейросеть: CNN-энкодер + Transformer-декодер
│   └── app/                   # FastAPI + Clean Architecture
│       ├── api/
│       ├── core/              # конфиг, загрузка весов, препроцессинг, визуализация
│       ├── domain/
│       └── adapters/
└── weights/
    └── best.pt                # предобученные веса (монтируются в planner-контейнер)
```

---

## Быстрый старт

### 1. Скопировать конфиг

```bash
cp .env.example .env
```

### 2. Запустить

```bash
docker compose up --build
```

Дождаться, пока все 4 контейнера перейдут в состояние `healthy`.

---

## Использование

### Сгенерировать карту

```bash
curl -X POST http://localhost:8000/generate \
  -H "Content-Type: application/json" \
  -d '{"height": 256, "width": 256, "seed": 42}'
```

Ответ:
```json
{"map_id": "<uuid>", "grid_size": [256, 256]}
```

### Построить маршрут

```bash
curl -X POST http://localhost:8001/plan \
  -H "Content-Type: application/json" \
  -d '{"map_id": "<uuid>", "start": [20, 20], "goal": [230, 230], "vessel_max_current": 1.0}'
```

Ответ:
```json
{
  "plan_id": "<uuid>",
  "waypoints": [[20, 20], [35, 42], "..."],
  "success": true,
  "n_repairs": 1,
  "visual_url": "/plans/<uuid>/image"
}
```

### Посмотреть визуализацию

```
http://localhost:8001/plans/<plan_id>/image
```

Или сохранить:
```bash
curl -o path.png http://localhost:8001/plans/<plan_id>/image
```

---

## API

### `POST /generate` (map-service, порт 8000)

| Поле | Тип | По умолчанию | Описание |
|------|-----|---|---|
| `height` | int | 128 | Высота карты в пикселях (128–1024) |
| `width` | int | 128 | Ширина карты в пикселях (128–1024) |
| `seed` | int | случайный | Seed для воспроизводимости |

### `POST /plan` (planner-service, порт 8001)

| Поле | Тип | По умолчанию | Описание |
|------|-----|---|---|
| `map_id` | str | — | UUID карты из `/generate` |
| `start` | [int, int] | — | Стартовая точка `[row, col]` |
| `goal` | [int, int] | — | Целевая точка `[row, col]` |
| `vessel_max_current` | float | 1.0 | Максимально допустимая скорость течения (м/с): `0.5` — лёгкий USV, `1.0` — средний, `2.0` — тяжёлый |

Поля ответа:
- `success` — достиг ли путь цели
- `n_repairs` — сколько сегментов заменено A* (при коллизии с сушей)

---

## Архитектура

```
Клиент ──POST /generate──> map-service ──upload .npz──> MinIO (maps/)
                                │
                                └──publish map.created──> RabbitMQ

Клиент ──POST /plan──> planner-service ──download .npz──> MinIO (maps/)
                            │            ──upload .png──> MinIO (visuals/)
                            └──publish plan.completed──> RabbitMQ

Клиент ──GET /plans/{id}/image──> planner-service ──stream PNG──> Клиент
```

### Нейросеть (`best.pt`)

- **Энкодер**: 5-слойный CNN сжимает карту (3 канала × H × W) в токены размерности 256
- **Декодер**: 4-слойный Transformer авторегрессивно предсказывает смещения `(Δrow, Δcol)` шаг за шагом
- При попадании в сушу — A* локально чинит конфликтный сегмент

### SOLID

В обоих сервисах одна структура:

```
domain/
├── ports.py    # Protocol-интерфейсы (StoragePort, BrokerPort, PlannerPort)
└── service.py  # use-case зависит только от портов — никакого boto3/aio-pika
adapters/       # конкретные реализации: S3, RabbitMQ
main.py         # Composition Root: создаёт адаптеры, инжектирует в use-case
```

---

## Инфраструктура

| Сервис | URL | Логин |
|--------|-----|-------|
| map-service API | http://localhost:8000 | — |
| planner-service API | http://localhost:8001 | — |
| MinIO UI | http://localhost:9001 | minioadmin / minioadmin |
| RabbitMQ UI | http://localhost:15672 | guest / guest |

---

## Отладка

```bash
# Логи сервисов
docker compose logs -f map-service
docker compose logs -f planner-service

# Остановить и сохранить данные MinIO
docker compose down

# Остановить и удалить все данные
docker compose down -v
```
