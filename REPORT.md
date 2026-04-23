# Подробный отчёт: микросервисная обёртка над pathformer

Этот документ объясняет, что было сделано, какая логика внутри и где что искать, чтобы дальше было проще копаться в коде.

---

## 1. Что это за проект в целом

**Pathformer** — это нейросеть (`best.pt`), которая планирует маршрут для беспилотного судна по карте моря: где суша, где течения. Сама по себе pathformer — это Python-библиотека. Чтобы её можно было использовать из других программ (по HTTP), её надо обернуть в сервис.

Было построено **два микросервиса** поверх pathformer:

1. **map-service** — генерирует карту (процедурно: случайные острова + течения) и сохраняет её в хранилище.
2. **planner-service** — берёт карту, пропускает через нейросеть, возвращает путь + PNG-картинку с результатом.

Они общаются друг с другом через **RabbitMQ** (события), а большие файлы хранят в **MinIO** (S3-совместимое объектное хранилище).

Всё запускается одной командой `docker compose up --build` из папки `microservices/`.

---

## 2. Структура репозитория

```
Project_net_vuz/
├── PLAN.md                 ← изначальное ТЗ + лог всех изменений по ходу работы
├── PROJECT_OVERVIEW.md     ← "что такое pathformer" простыми словами
├── REPORT.md               ← этот файл
│
├── pathformer/             ← СТАРЫЙ проект. Мы его НЕ ТРОГАЕМ.
│   ├── src/pathformer/     ← исходники нейросети (USVPlanner)
│   ├── synthetic_generator/← генератор карт (generate_map)
│   └── best.pt             ← обученные веса (монтируются в planner-service)
│
└── microservices/          ← ВСЯ новая программа
    ├── docker-compose.yml
    ├── .env.example
    ├── README.md
    ├── shared/             ← общие pydantic-модели событий
    │   └── events.py
    ├── map-service/        ← сервис генерации карт
    └── planner-service/    ← сервис планирования маршрута
```

Ключевая идея: `pathformer/` — это **зависимость** (как сторонняя библиотека), мы её импортируем, но не модифицируем. Все свои изменения — только в `microservices/`.

---

## 3. Из чего состоит стек (docker-compose.yml)

Четыре контейнера поднимаются вместе:

| Контейнер | Порт | Что делает | UI для отладки |
|-----------|------|-----------|---------------|
| **rabbitmq** | 5672 (AMQP), 15672 (UI) | Брокер сообщений. Хранит exchange `pathformer` и маршрутизирует события. | http://localhost:15672 (guest/guest) |
| **minio** | 9000 (S3 API), 9001 (UI) | S3-хранилище для двух бакетов: `maps/` (сырые карты `.npz`) и `visuals/` (PNG визуализаций). | http://localhost:9001 (minioadmin/minioadmin) |
| **map-service** | 8000 | HTTP API для генерации карт. | http://localhost:8000/docs (Swagger) |
| **planner-service** | 8001 | HTTP API для построения пути + отдача картинок. | http://localhost:8001/docs |

`depends_on` + `healthcheck` гарантируют, что сервисы не стартуют пока rabbitmq и minio не готовы принимать соединения.

В `docker-compose.yml` важный момент:
```yaml
build:
  context: ..     ← контекст сборки — РОДИТЕЛЬСКАЯ папка
  dockerfile: microservices/map-service/Dockerfile
volumes:
  - ../pathformer/best.pt:/app/weights/best.pt:ro   ← монтируем веса из соседней папки
```

Это позволяет Dockerfile'у видеть и `microservices/` (свой код), и `pathformer/` (чужой код + веса) одновременно.

---

## 4. Поток данных: полный цикл одного запроса

```
┌──────────┐
│  Клиент  │ (curl / Swagger UI / браузер)
└────┬─────┘
     │
     │ 1. POST http://localhost:8000/generate
     │    body: {"height":128, "width":128, "seed":42}
     │
     ▼
┌────────────┐
│map-service │
│            │   2. generate_map(128, 128, 42)   ← вызов pathformer
│            │      возвращает {land_mask, current_intensity, current_direction}
│            │
│            │   3. np.savez_compressed → .npz bytes
│            │
│            │   4. upload(bucket="maps", key="<uuid>.npz")
│            │   ──────────────────────────► MinIO
│            │
│            │   5. publish("map.created", {map_id, bucket, key, grid_size, ...})
│            │   ──────────────────────────► RabbitMQ exchange "pathformer"
│            │
│            │   6. return {"map_id": "<uuid>", "grid_size": [128, 128]}
└────────────┘
     │
     │  <── клиент получил map_id
     │
     │
     │ 7. POST http://localhost:8001/plan
     │    body: {"map_id":"<uuid>", "start":[10,10], "goal":[120,120], "vessel_max_current":1.0}
     │
     ▼
┌────────────────┐
│planner-service │  8. download(bucket="maps", key="<uuid>.npz")
│                │     ◄─────────────────────── MinIO
│                │
│                │  9. np.load → dict с тремя массивами
│                │
│                │ 10. preprocessing: (land_mask, intensity, direction)
│                │     → (u, v, safety_field) 3-канальный тензор
│                │
│                │ 11. lazy-load best.pt (только первый раз, ~50мс)
│                │
│                │ 12. нормализация start/goal: (row/127, col/127)
│                │     нормализация vessel_max_current / 3.0
│                │
│                │ 13. plan_path_with_repair(model, x_map, start, goal, vessel)
│                │     → (waypoints, success, n_repairs)
│                │
│                │ 14. денормализация waypoints обратно в пиксели
│                │
│                │ 15. render_plan(...) — matplotlib → PNG bytes
│                │
│                │ 16. upload(bucket="visuals", key="<plan_id>.png")
│                │     ──────────────────────────► MinIO
│                │
│                │ 17. publish("plan.completed", {plan_id, map_id, n_repairs, ...})
│                │     ──────────────────────────► RabbitMQ
│                │
│                │ 18. return {plan_id, waypoints, success, n_repairs, visual_url}
└────────────────┘
     │
     │ <── клиент получил JSON
     │
     │ 19. GET http://localhost:8001/plans/<plan_id>/image
     │     (открыть в браузере)
     ▼
┌────────────────┐
│planner-service │ 20. download(bucket="visuals", key="<plan_id>.png")
│                │     ◄─────────────────────── MinIO
│                │
│                │ 21. вернуть PNG (Content-Type: image/png)
└────────────────┘
```

**Важный момент: почему два канала связи (RabbitMQ + MinIO)?**

- **MinIO** используется для передачи **данных** (сырые карты и PNG — десятки килобайт до мегабайт).
- **RabbitMQ** передаёт только **уведомления-события** (map_id, plan_id и метаданные — сотни байт).

Пихать мегабайтные файлы через брокер сообщений — антипаттерн: он забьётся. Поэтому стандартная схема: файл в S3, указатель на файл в сообщении.

---

## 5. map-service: внутреннее устройство

**Каталог:** [microservices/map-service/app/](microservices/map-service/app/)

Слои (сверху вниз — от внешнего к бизнес-логике):

### 5.1. API (HTTP уровень)

[app/api/routes.py](microservices/map-service/app/api/routes.py)

```python
@router.post("/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest, request: Request):
    use_case = request.app.state.generate_use_case
    seed = req.seed if req.seed is not None else secrets.randbits(32)
    result = await use_case.execute(req.height, req.width, seed)
    return GenerateResponse(map_id=result.map_id, grid_size=result.grid_size)
```

Ответственность: парсить HTTP, вызвать use-case, отформатировать ответ. Ничего больше.

### 5.2. Domain (бизнес-логика)

[app/domain/ports.py](microservices/map-service/app/domain/ports.py) — Protocol-интерфейсы (абстракции):

```python
class StoragePort(Protocol):
    async def upload(self, bucket: str, key: str, data: bytes) -> None: ...

class BrokerPort(Protocol):
    async def publish(self, routing_key: str, payload: dict) -> None: ...

class MapGeneratorPort(Protocol):
    def generate(self, height: int, width: int, seed: int) -> dict: ...
```

[app/domain/service.py](microservices/map-service/app/domain/service.py) — сам use-case:

```python
class GenerateMapUseCase:
    def __init__(self, generator, storage, broker, bucket, max_current):
        ...

    async def execute(self, height, width, seed) -> GenerateResult:
        arrays = self._generator.generate(height, width, seed)
        npz_bytes = self._serialize_npz(arrays)
        map_id = str(uuid.uuid4())
        await self._storage.upload(self._bucket, f"{map_id}.npz", npz_bytes)
        event = MapCreated(...)
        await self._broker.publish(ROUTING_KEY_MAP_CREATED, event.model_dump())
        return GenerateResult(map_id=map_id, grid_size=(height, width))
```

**Важно:** этот класс не импортирует ни `boto3`, ни `aio-pika`. Он работает через абстрактные порты. Это означает:
- Его можно тестировать с fake-объектами вместо S3 и RabbitMQ.
- Бизнес-логика не меняется, если завтра заменить MinIO на Amazon S3 или RabbitMQ на Kafka.

### 5.3. Adapters (конкретные реализации)

[app/adapters/storage.py](microservices/map-service/app/adapters/storage.py) — `S3Storage` на `boto3`.
[app/adapters/broker.py](microservices/map-service/app/adapters/broker.py) — `RabbitBroker` на `aio-pika`.

boto3 синхронный → оборачиваем вызовы в `asyncio.to_thread` чтобы не блокировать FastAPI event loop.

### 5.4. Core (обёртка над pathformer)

[app/core/generator.py](microservices/map-service/app/core/generator.py):

```python
from generate_map import generate_map

class PathformerMapGenerator:
    def generate(self, height, width, seed):
        return generate_map(height=height, width=width, seed=seed)
```

Это мост между миром pathformer и нашим портом `MapGeneratorPort`.

### 5.5. main.py — композиционный корень

[app/main.py](microservices/map-service/app/main.py) — единственное место, где все части соединяются:

```python
@asynccontextmanager
async def lifespan(app):
    broker = RabbitBroker(...)
    await broker.connect()
    storage = S3Storage(...)
    generator = PathformerMapGenerator()

    app.state.generate_use_case = GenerateMapUseCase(
        generator=generator,
        storage=storage,
        broker=broker,
        ...
    )
    yield
    await broker.close()
```

FastAPI `lifespan` гарантирует, что RabbitMQ-соединение поднимется до первого запроса и закроется при shutdown.

---

## 6. planner-service: внутреннее устройство

**Каталог:** [microservices/planner-service/app/](microservices/planner-service/app/)

Структура такая же (api/domain/adapters/core), но ядро сложнее — тут нейросеть, препроцессинг, визуализация.

### 6.1. API

[app/api/routes.py](microservices/planner-service/app/api/routes.py)

Три эндпоинта:
- `POST /plan` — запустить планирование
- `GET /plans/{plan_id}/image` — получить PNG
- `GET /health`

Первый — основная логика, второй — прокси к MinIO:
```python
@router.get("/plans/{plan_id}/image")
async def plan_image(plan_id, request):
    storage = request.app.state.storage
    png = await storage.download("visuals", f"{plan_id}.png")
    return Response(content=png, media_type="image/png")
```

### 6.2. Core (самое важное)

#### [app/core/preprocessor.py](microservices/planner-service/app/core/preprocessor.py)

Конвертирует сырую карту `{land_mask, intensity, direction}` в 3-канальный тензор `(u, v, safety_field)`, который ждёт нейросеть.

```python
u = intensity * cos(direction) / max_current_global   # ~[-1, 1]
v = intensity * sin(direction) / max_current_global   # ~[-1, 1]
safety_field = 1 - clip(distance_to_land / 20, 0, 1)  # [0, 1]
```

- `u, v` — декартовы компоненты вектора течения (где + куда силой какой)
- `safety_field` — "опасность": 1 на суше и рядом с берегом, 0 в открытом море (`distance_transform_edt` считает расстояние от воды до ближайшей суши, нормируется)

**Формула один-в-один** с `pathformer/src/pathformer/data/dataset.py:70-84` — то, что модель видела на обучении. Любое отклонение → модель работает хуже.

Плюс fail-fast проверка размера: карты > 256×256 не поддерживаются (pretrained PE precomputed только на 16×16 токенов = 256×256 пикселей после encoder'а).

#### [app/core/planner.py](microservices/planner-service/app/core/planner.py)

Lazy-load `best.pt` и вызов модели:

```python
class ModelPlanner:
    def _ensure_loaded(self):
        # вызывается один раз при первом /plan
        ckpt = torch.load(self._weights_path, ..., weights_only=False)
        config = ckpt["config"]

        # Защита: канонический pathformer переписывает goal_threshold
        # если он битый в ckpt (prepare_config_for_evaluation)
        if config.goal_threshold <= 0 or config.goal_threshold > 0.25:
            config.goal_threshold = 0.04

        model = USVPlanner(config)
        model.load_state_dict(ckpt["model_state_dict"])
        model.eval()
        return model

    def plan(self, map_tensor, start_norm, goal_norm, vessel_class):
        model = self._ensure_loaded()
        # ... подготовить тензоры ...
        with torch.inference_mode():
            waypoints, success, n_repairs = plan_path_with_repair(
                model, x_map, start, goal, vc, model.config
            )
```

**Ключевой момент:** мы вызываем `plan_path_with_repair`, а не `model.plan()`. Это каноничный способ из `pathformer/scripts/evaluate.py`:
1. Нейросеть строит сырой путь (может зацепить сушу).
2. **A\* repair** локально переложит те сегменты, которые пересекают сушу, через обход.

Без repair путь часто "упирался" в острова — это и был красный зигзаг на первом скриншоте.

#### [app/core/visualizer.py](microservices/planner-service/app/core/visualizer.py)

matplotlib (backend `Agg` — headless) рисует карту + путь → PNG bytes в памяти.

Что изображается:
- **Фон** — сила течений (`sqrt(u² + v²)`), синим градиентом.
- **Суша** — жёлто-коричневым поверх.
- **Стрелки течения** — `quiver`, subsampled (~20 стрелок по стороне, иначе месиво).
- **Путь** — синий если `success=True`, красный если `False`.
- **Старт** — зелёный квадрат.
- **Цель** — красная звезда.
- **Заголовок** — `success=..., N waypoints, K repairs` (сколько сегментов пофиксил A\*).

Buffer `BytesIO` → байты → потом заливаем в MinIO.

### 6.3. Domain

[app/domain/service.py](microservices/planner-service/app/domain/service.py) — `PlanPathUseCase` координирует весь пайплайн:

```python
async def execute(self, map_id, start_pixels, goal_pixels, vessel_max_current):
    # 1. Скачать карту
    npz_bytes = await self._storage.download("maps", f"{map_id}.npz")
    arrays = load_arrays(npz_bytes)
    H, W = arrays["land_mask"].shape

    # 2. Нормализовать vessel_class как при обучении
    vessel_class_normalized = vessel_max_current / self._max_current  # 1.0 / 3.0 = 0.333

    # 3. Вся inference-часть — в thread, чтобы не блокировать event loop
    def _run():
        map_tensor = to_model_tensor(arrays, self._max_current)
        start_norm = (start_pixels[0]/(H-1), start_pixels[1]/(W-1))
        goal_norm = (goal_pixels[0]/(H-1), goal_pixels[1]/(W-1))

        plan_out = self._planner.plan(map_tensor, start_norm, goal_norm, vessel_class_normalized)

        # Обратная нормализация: [0,1] → пиксели
        wp_pixels = plan_out.waypoints_normalized * np.array([H-1, W-1], dtype=np.float32)

        png = render_plan(..., waypoints_pixels=wp_pixels, ...)
        return plan_out, wp_pixels, png

    plan_out, wp_pixels, png = await asyncio.to_thread(_run)

    # 4. Загрузить PNG
    plan_id = str(uuid.uuid4())
    await self._storage.upload("visuals", f"{plan_id}.png", png)

    # 5. Опубликовать событие
    event = PlanCompleted(plan_id=plan_id, map_id=map_id, ..., n_repairs=plan_out.n_repairs)
    await self._broker.publish(ROUTING_KEY_PLAN_COMPLETED, event.model_dump())

    return PlanResult(...)
```

---

## 7. SOLID на практике

| Принцип | Где в коде |
|---------|-----------|
| **SRP** (один класс — одна ответственность) | `generator.py` только генерит; `storage.py` только в S3; `broker.py` только в RabbitMQ; `service.py` только координирует. Никто не совмещает обязанности. |
| **OCP** (расширение без модификации) | Чтобы добавить поддержку Kafka — пишется новый класс `KafkaBroker`, реализующий `BrokerPort`. `service.py` ничего не знает об изменении. |
| **LSP** (взаимозаменяемость) | Любой объект с методом `publish(routing_key, payload)` сойдёт за `BrokerPort`. Use-case не проверяет тип. |
| **ISP** (узкие интерфейсы) | `StoragePort` не содержит методов брокера. `BrokerPort` — отдельно. Никто не реализует методы, которые ему не нужны. |
| **DIP** (зависимости от абстракций) | `PlanPathUseCase` зависит от `StoragePort`, `BrokerPort`, `PlannerPort` — только `Protocol`'ы. Конкретные `S3Storage`, `RabbitBroker`, `ModelPlanner` инжектятся в `main.py` — composition root. |

**Практическая польза:** unit-тесты тривиальны. Пишешь `class FakeStorage` с `upload/download`, передаёшь в `PlanPathUseCase(...)`, проверяешь поведение без Docker и без сети.

---

## 8. Shared — общие модели событий

[microservices/shared/events.py](microservices/shared/events.py)

Pydantic-модели событий, которые публикуются в RabbitMQ. Они разделяемы между сервисами:

```python
class MapCreated(BaseModel):
    event: str = "map.created"
    map_id: str
    bucket: str
    key: str
    grid_size: tuple[int, int]
    max_current: float
    seed: int
    ts: str = datetime.utcnow().isoformat()

class PlanCompleted(BaseModel):
    event: str = "plan.completed"
    plan_id: str
    map_id: str
    waypoints_count: int
    success: bool
    n_repairs: int = 0
    visual_bucket: str
    visual_key: str
    ts: str
```

Валидация на стороне publisher'а (перед отправкой) и consumer'а (при чтении) — чтобы схема сообщений не разошлась.

Exchange `pathformer` типа `topic`, routing keys:
- `map.created` — публикует map-service
- `plan.completed` — публикует planner-service

Сейчас **нет consumer'ов** (никто не слушает эти события). Это заготовка на будущее: можно добавить аналитику, прогрев кэша, retry-логику — подписавшись на очередь.

---

## 9. Dockerfile'ы — что происходит при сборке

### map-service Dockerfile

```dockerfile
FROM python:3.11-slim

# Системные зависимости для scipy
RUN apt-get install -y build-essential

# Зависимости Python
COPY microservices/map-service/requirements.txt .
RUN pip install -r requirements.txt

# pathformer generator (без torch — только для карт)
COPY pathformer/synthetic_generator ./pathformer/synthetic_generator

# Наш код
COPY microservices/shared ./shared
COPY microservices/map-service/app ./map-service/app

ENV PYTHONPATH=/app:/app/pathformer/synthetic_generator

CMD ["uvicorn", "app.main:app", "--app-dir", "/app/map-service", "--host", "0.0.0.0", "--port", "8000"]
```

- `PYTHONPATH` = `/app` (чтобы `shared` и `app` находились) + `/app/pathformer/synthetic_generator` (чтобы `from generate_map import generate_map` работало).
- `--app-dir /app/map-service` — потому что имя папки `map-service` с дефисом, его нельзя import'нуть как модуль напрямую.

### planner-service Dockerfile

```dockerfile
FROM python:3.12-slim
ENV MPLBACKEND=Agg  # matplotlib без дисплея

# Отдельно torch CPU-wheel (чтобы не тащить CUDA в образ)
RUN pip install torch==2.5.0 --index-url https://download.pytorch.org/whl/cpu

COPY microservices/planner-service/requirements.txt .
RUN pip install -r requirements.txt

# Исходники pathformer (весь src, ядро модели)
COPY pathformer/src ./pathformer/src
COPY pathformer/synthetic_generator ./pathformer/synthetic_generator

COPY microservices/shared ./shared
COPY microservices/planner-service/app ./planner-service/app

RUN mkdir -p /app/weights
# best.pt монтируется из docker-compose.yml volume'ом в /app/weights/best.pt

ENV PYTHONPATH=/app:/app/pathformer/src

CMD ["uvicorn", "app.main:app", "--app-dir", "/app/planner-service", ...]
```

- torch CPU wheel устанавливается отдельно из специального индекса — чтобы образ не тянул 2ГБ CUDA-библиотек.
- `best.pt` монтируется volume'ом (`../pathformer/best.pt:/app/weights/best.pt:ro`), а не копируется в образ — можно подменять веса без пересборки.

---

## 10. Как работает система SAN (storage + broker)

### MinIO

- Адрес внутри docker-network: `minio:9000`. Снаружи: `localhost:9000` (S3 API) и `localhost:9001` (веб-UI).
- Два бакета создаются **лениво** — при первой записи. Метод `ensure_bucket` в адаптере делает `head_bucket` → если ошибка, делает `create_bucket`.
- Данные хранятся в docker volume `minio_data` (персистентно между перезапусками, пока не `docker compose down -v`).

### RabbitMQ

- Адрес: `amqp://guest:guest@rabbitmq:5672/`. UI на `localhost:15672`.
- Один **exchange** типа `topic` с именем `pathformer`. Сообщения маршрутизируются по routing_key.
- `aio-pika` с `publisher_confirms=True` — публикация подтверждённая (broker ACK'нет прежде чем вернуть `publish()`).
- Сообщения `delivery_mode=PERSISTENT` — переживают рестарт брокера.

---

## 11. Специфика nuances pathformer'а (чтобы судно реально плавало)

Эта секция — про точное соответствие нашего inference'а тому, как модель обучалась.

### 11.1. Нормализация координат

Модель на обучении видит waypoints в диапазоне `[0, 1]`, не в пикселях. В `dataset.py:199`:
```python
waypoints = waypoints / np.array([H-1, W-1], dtype=np.float32)
```

Мы в `service.py:75-76`:
```python
start_norm = (start_pixels[0]/(H-1), start_pixels[1]/(W-1))
goal_norm = (goal_pixels[0]/(H-1), goal_pixels[1]/(W-1))
```

И обратно — умножаем на `(H-1, W-1)` чтобы показать пользователю пиксельные координаты.

### 11.2. Нормализация vessel_class

Обучение: `vessel_class = vessel_max_current / max_current_global`. Training-значения:
- light_usv (0.5 м/с) → 0.5 / 3.0 = 0.167
- medium_usv (1.0 м/с) → 1.0 / 3.0 = 0.333
- heavy_usv (2.0 м/с) → 2.0 / 3.0 = 0.667

Наш API принимает `vessel_max_current` в м/с (как в `VESSEL_CLASSES`), и в `service.py:68` делит на `max_current_global=3.0`. До этого исправления мы подавали значение as-is — модель получала 1.0 (= "вымышленное судно сильнее heavy_usv") и работала плохо.

### 11.3. Порядок каналов

Модель ожидает `[u, v, safety_field]` — именно в таком порядке. Любая перестановка сломает inference. Проверено в `preprocessor.py:35`:
```python
return np.stack([u, v, safety_field], axis=0)
```

### 11.4. Collision repair

Сырой путь от нейросети часто пересекает сушу. `plan_path_with_repair()` из `pathformer/inference.py:66-97`:
1. Вызывает обычный `plan_path()`.
2. Строит binary land_mask из `safety_field > 0.95`.
3. Запускает `repair_path()` — локальный A\* обход через `skimage.graph.MCP_Geometric` для сегментов, которые режут сушу.
4. Возвращает `(path, success, n_repairs)`.

Мы вызываем именно эту функцию, а не raw `model.plan()`. Это решило проблему зигзага возле острова.

### 11.5. goal_threshold guard

В pathformer `PlannerConfig.goal_threshold=10` (training default). В inference'е сравнивается с нормализованной дистанцией — где максимум ~1.41. Если в `ckpt["config"]` сохранилось `10`, то на первом же шаге `dist < 10` → return `success=True` с путём `[start, goal]` (прямая линия, игнорируя всё).

Каноничный `prepare_config_for_evaluation()` в `evaluation.py:83-91` форсирует замену:
```python
if config.goal_threshold <= 0 or config.goal_threshold > 0.25:
    config.goal_threshold = 0.04  # DEFAULT_GOAL_THRESHOLD
```

Мы делаем то же самое в `planner.py:44-51` — защита от сломанных чекпоинтов.

---

## 12. Как отладить проблему

Если путь кривой, успех не `true`, или что-то не работает:

1. **Логи контейнеров:**
   ```bash
   docker compose logs -f planner-service
   docker compose logs -f map-service
   ```

2. **Данные в MinIO** — http://localhost:9001 → бакет `maps` / `visuals`. Можно скачать `.npz` файл и посмотреть глазами в Python:
   ```python
   import numpy as np
   d = np.load("<map_id>.npz")
   print(d["land_mask"].shape, d["current_intensity"].mean())
   ```

3. **Очередь RabbitMQ** — http://localhost:15672 → Exchanges → pathformer. Видно сколько событий опубликовано.

4. **Swagger UI** — http://localhost:8001/docs. Tab "Try it out" у каждого эндпоинта — удобно для ручного теста.

5. **Пересборка после правок:**
   ```bash
   docker compose up --build
   ```
   Без `--build` будут использованы старые образы.

6. **Полный reset (стереть MinIO):**
   ```bash
   docker compose down -v
   docker compose up --build
   ```

---

## 13. Что можно добавить дальше

1. **Subscriber на события** — на данный момент `map.created` и `plan.completed` публикуются, но никто не слушает. Можно:
   - Добавить analytics-сервис, который считает метрики.
   - planner-service может подписаться на `map.created` и кэшировать карты в памяти, чтобы не скачивать их при каждом `/plan`.

2. **Пакетная обработка** — позволить в одном `/plan` передать список start/goal пар.

3. **Кэш путей** — если пришёл запрос с тем же `(map_id, start, goal, vessel_class)`, возвращать сохранённый результат.

4. **Metrics / tracing** — OpenTelemetry, Prometheus.

5. **Авторизация** — JWT / API keys.

6. **Тесты** — unit-тесты на use-case'ы с fake-портами + integration тесты через `testcontainers`.

7. **GPU inference** — добавить опцию `device=cuda` для скорости.

---

## 14. Хронология изменений (из PLAN.md)

- **Wave 1**: инфраструктура — docker-compose, Dockerfile'ы, каркасы сервисов
- **Wave 2a**: map-service /generate + RabbitMQ publish
- **Wave 2b**: planner-service /plan + MinIO + visualizer + publish
- **Wave 3**: README + smoke-тест
- **Wave 4**: реорганизация в `pathformer/` vs `microservices/`
- **Wave 5**: фикс vessel_class → `vessel_max_current` с нормализацией `/ max_current_global`
- **Wave 6**: `plan_path_with_repair` + `goal_threshold` guard + size assert

---

## 15. Краткий cheatsheet

```bash
# Запустить
cd microservices
docker compose up --build

# Остановить
docker compose down

# Стереть данные MinIO и перезапустить
docker compose down -v && docker compose up --build

# Логи
docker compose logs -f <service>
```

**URL'ы:**
- Swagger map-service: http://localhost:8000/docs
- Swagger planner-service: http://localhost:8001/docs
- Картинка пути: http://localhost:8001/plans/{plan_id}/image
- MinIO UI: http://localhost:9001 (minioadmin/minioadmin)
- RabbitMQ UI: http://localhost:15672 (guest/guest)

**Pipeline:**
```
POST /generate {seed} → map_id
POST /plan {map_id, start, goal, vessel_max_current} → plan_id + waypoints
GET /plans/{plan_id}/image → PNG
```
