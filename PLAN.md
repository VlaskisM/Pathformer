Нужно начать с малого

Нужно бернуть в микросервисы следующие программы 
• получение карты (набора карт) 
• построение траектории по полученной карте (набору карт)


У меня есть файлик с лучшими весам в этом проекте best.pt. Мне нужно чтобы ты эти веса использовал при генерации пути.

Нужно построить программу по микросервисной архитектуре, строить так, чтобы выполнялись требования SOLID, а также разделялась логика.

Общение между микросервисами должно выполняться с помощью RubbitMQ

А сгенерированные карты (набора карт) должны записываться в S3 Minio

Если я что-то не продумал, то скажи мне об этом. Дописывай новую вводную информацию в этот файлик.


Мне не понятно, как ты реализуешь вывод графиков

---

# Отчёт о ходе реализации

## Архитектурные решения

**Сервисы:**
- `map-service` (порт 8000) — генерация карт. Python 3.11 + numpy + scipy + FastAPI.
- `planner-service` (порт 8001) — построение траектории. Python 3.12 + torch CPU + matplotlib + FastAPI.

**Инфраструктура:**
- RabbitMQ (3.13-management) — порты 5672 (AMQP) + 15672 (UI).
- MinIO (S3-совместимое хранилище) — порты 9000 (S3 API) + 9001 (UI).

**Поток данных:**
1. Клиент → `POST /generate` → map-service генерирует карту через `generate_map()`, сохраняет `.npz` в MinIO (`maps/{map_id}.npz`), публикует `map.created` в RabbitMQ, возвращает `{map_id, grid_size}`.
2. Клиент → `POST /plan` с `{map_id, start, goal, vessel_class}` → planner-service скачивает карту из MinIO, конвертирует в 3-channel tensor `(u, v, safety_field)`, загружает `best.pt` (lazy, единожды), запускает `model.plan()`, рендерит PNG через matplotlib, сохраняет в MinIO (`visuals/{plan_id}.png`), публикует `plan.completed`, возвращает `{plan_id, waypoints, success, visual_url}`.
3. Клиент → `GET /plans/{plan_id}/image` → planner-service отдаёт PNG напрямую.

**Почему такая схема RabbitMQ/MinIO:**
- Через RabbitMQ — только события/уведомления (`map.created`, `plan.completed`), это то для чего она предназначена.
- Большие бинарные артефакты (карты `.npz`, визуализации `.png`) — через MinIO. Передавать 100KB+ файлы через message broker — антипаттерн.

**SOLID на практике:**
- SRP: в каждом сервисе разделение на `domain/` (use-case'ы), `adapters/` (MinIO, RabbitMQ), `core/` (генерация/inference/preprocessing), `api/` (HTTP routes).
- DIP: use-case'ы зависят от `Protocol`-интерфейсов (`StoragePort`, `BrokerPort`), конкретные реализации инжектятся в `main.py`.
- OCP/ISP: порты раздельные и узкоспециализированные — можно заменить S3 на FS или RabbitMQ на Kafka без правки бизнес-логики.

**Визуализация (ответ на вопрос пользователя):**
matplotlib с backend `Agg` (headless) рисует карту + путь в `BytesIO`, полученный PNG сохраняется в MinIO под ключом `visuals/{plan_id}.png`. Endpoint `GET /plans/{plan_id}/image` отдаёт его напрямую из S3 — открывается в браузере по URL. В JSON-ответе возвращается относительный URL.

Что рисуется:
- Фон: величина течений (`sqrt(u²+v²)`) синим градиентом (cmap=Blues).
- Суша — жёлто-коричневым overlay (cmap=YlOrBr).
- Quiver-стрелки показывают направление течений (subsampled).
- Путь (waypoints) — синий если `success=true`, красный если `false`.
- Зелёный квадрат — start, красная звезда — goal.

## Ход работы

### Wave 1: Инфраструктура ✓

- [.env.example](.env.example) — переменные окружения
- [docker-compose.yml](docker-compose.yml) — 4 сервиса (rabbitmq, minio, map-service, planner-service) с healthcheck и depends_on
- [shared/events.py](shared/events.py) — Pydantic-модели событий `MapCreated` / `PlanCompleted` + константы routing_key и exchange

### Wave 2a: map-service ✓

- [map-service/Dockerfile](map-service/Dockerfile) — python:3.11-slim + scipy + numpy. Копирует `pathformer/synthetic_generator/` и `shared/`. `PYTHONPATH=/app:/app/pathformer/synthetic_generator`.
- [map-service/requirements.txt](map-service/requirements.txt) — fastapi, uvicorn, numpy, scipy, boto3, aio-pika, pydantic-settings, h5py, scikit-image. **Без torch.**
- [map-service/app/domain/ports.py](map-service/app/domain/ports.py) — Protocol-интерфейсы `StoragePort`, `BrokerPort`, `MapGeneratorPort`
- [map-service/app/domain/service.py](map-service/app/domain/service.py) — `GenerateMapUseCase`: генерирует карту → сериализует в `.npz` через `np.savez_compressed` → загружает в MinIO `maps/{map_id}.npz` → публикует `map.created` в RabbitMQ → возвращает `{map_id, grid_size}`
- [map-service/app/core/generator.py](map-service/app/core/generator.py) — `PathformerMapGenerator` обёртка над `pathformer.synthetic_generator.generate_map.generate_map()`
- [map-service/app/adapters/storage.py](map-service/app/adapters/storage.py) — `S3Storage` (boto3, async через asyncio.to_thread)
- [map-service/app/adapters/broker.py](map-service/app/adapters/broker.py) — `RabbitBroker` (aio-pika, topic exchange, publisher confirms, persistent delivery)
- [map-service/app/api/routes.py](map-service/app/api/routes.py) — `POST /generate`, `GET /health`
- [map-service/app/main.py](map-service/app/main.py) — composition root, FastAPI lifespan управляет подключениями

### Wave 2b: planner-service ✓

- [planner-service/Dockerfile](planner-service/Dockerfile) — python:3.12-slim + torch CPU wheel из https://download.pytorch.org/whl/cpu. Копирует `pathformer/src/` и `pathformer/synthetic_generator/`. `PYTHONPATH=/app:/app/pathformer/src`. `MPLBACKEND=Agg` для headless-matplotlib.
- [planner-service/requirements.txt](planner-service/requirements.txt) — всё из map-service + matplotlib + tqdm
- [planner-service/app/core/preprocessor.py](planner-service/app/core/preprocessor.py) — `load_arrays` (распаковка `.npz`) + `to_model_tensor` (формула u/v/safety_field из [pathformer/src/pathformer/data/dataset.py:70-84](pathformer/src/pathformer/data/dataset.py))
- [planner-service/app/core/planner.py](planner-service/app/core/planner.py) — `ModelPlanner`: lazy-load `best.pt` под потокобезопасным lock'ом, `model.plan()` в `torch.inference_mode()`
- [planner-service/app/core/visualizer.py](planner-service/app/core/visualizer.py) — matplotlib Agg → PNG bytes. Рисует течения (Blues), сушу (YlOrBr), quiver-стрелки, путь (синий/красный), старт/цель
- [planner-service/app/domain/ports.py](planner-service/app/domain/ports.py) — Protocol-интерфейсы
- [planner-service/app/domain/service.py](planner-service/app/domain/service.py) — `PlanPathUseCase`: download карты → препроцессинг → inference → рендер PNG → upload → publish `plan.completed`. Inference+рендер запускаются в отдельном thread через `asyncio.to_thread` чтобы не блокировать event loop.
- [planner-service/app/adapters/storage.py](planner-service/app/adapters/storage.py) — S3Storage с `upload` + `download`
- [planner-service/app/adapters/broker.py](planner-service/app/adapters/broker.py) — RabbitBroker (копия из map-service)
- [planner-service/app/api/routes.py](planner-service/app/api/routes.py) — `POST /plan`, `GET /plans/{plan_id}/image`, `GET /health`
- [planner-service/app/main.py](planner-service/app/main.py) — composition root

### Wave 3: Документация + smoke-тест (в процессе)

- [microservices/README.md](microservices/README.md) — инструкции запуска, API, архитектура, отладка
- Сборка `cd microservices && docker compose up --build` и ручной тест

### Wave 4: Реорганизация в 2 папки ✓

По запросу пользователя разделено на две папки на верхнем уровне:
- `pathformer/` — старый проект (как был)
- `microservices/` — вся новая программа (`docker-compose.yml`, `map-service/`, `planner-service/`, `shared/`, `.env.example`, `README.md`)

Правки в конфигурации сборки:
- [microservices/docker-compose.yml](microservices/docker-compose.yml) — `context: ..` и `dockerfile: microservices/{map|planner}-service/Dockerfile`, volume `../pathformer/best.pt:/app/weights/best.pt:ro`
- [microservices/map-service/Dockerfile](microservices/map-service/Dockerfile), [microservices/planner-service/Dockerfile](microservices/planner-service/Dockerfile) — пути `COPY` с префиксом `microservices/` для своего кода; `COPY pathformer/...` без изменений (контекст теперь видит обе папки)

Docs (`PLAN.md`, `PROJECT_OVERVIEW.md`) оставлены в корне — они описывают проект целиком.

### Wave 5: Фикс vessel_class (после первого запуска) ✓

После первого успешного запуска пользователь прислал скриншот: путь красный (success=false), модель зигзагится возле цели. При проверке соответствия нашего кода и `pathformer/src/pathformer/data/dataset.py:202-204` нашёлся баг:

**Проблема:** в pathformer `vessel_class`, подаваемый в модель, **нормализован** — `vessel_max_current / max_current_global`. Training values:
- light_usv (0.5 м/с): 0.167
- medium_usv (1.0 м/с): 0.333
- heavy_usv (2.0 м/с): 0.667

Наш API принимал `vessel_class=1.0` и передавал as-is. Для модели это судно с `max_current=3.0` м/с — вне обучающего распределения, мощнее всех training-классов. Модель экстраполировала странно.

**Фикс:**
- Переименовано поле API: `vessel_class` → `vessel_max_current` (в м/с, как у VESSEL_CLASSES в pathformer)
- [microservices/planner-service/app/domain/service.py](microservices/planner-service/app/domain/service.py) делит на `max_current_global` перед подачей в `ModelPlanner.plan()`
- [microservices/planner-service/app/api/routes.py](microservices/planner-service/app/api/routes.py) — новая сигнатура + подсказки в Swagger UI
- [microservices/README.md](microservices/README.md) — обновлён пример и таблица параметров

Теперь при отправке `vessel_max_current=1.0` модель получает 0.333 (ровно как medium_usv на обучении).

### Wave 6: Выравнивание по каноничному inference ✓

После повторного аудита `microservices/planner-service/` vs `pathformer/src/pathformer/evaluation.py` найдены два реальных расхождения с каноничным inference (остальные проверки — OK, см. план `inherited-wibbling-swan.md`).

**1. Отсутствовал collision repair.** Каноничный `evaluate.py` всегда оборачивает `plan_path()` в `repair_path()` (локальный A* обход земли). Без этого нейросеть упирается в острова — именно этот зигзаг был на последнем скриншоте.

Правки:
- [microservices/planner-service/app/core/planner.py](microservices/planner-service/app/core/planner.py) — теперь вызывает `pathformer.inference.plan_path_with_repair()` вместо `model.plan()`. Возвращает `(waypoints, success, n_repairs)`.
- [microservices/planner-service/app/domain/service.py](microservices/planner-service/app/domain/service.py) — пробрасывает `n_repairs` в `PlanResult`.
- [microservices/planner-service/app/api/routes.py](microservices/planner-service/app/api/routes.py), [microservices/shared/events.py](microservices/shared/events.py) — поле `n_repairs` в HTTP-ответе и событии `plan.completed`.
- [microservices/planner-service/app/core/visualizer.py](microservices/planner-service/app/core/visualizer.py) — `n_repairs` в заголовке PNG.

**2. Защитная нормализация `goal_threshold`.** Каноничный `prepare_config_for_evaluation()` форсированно переписывает `goal_threshold` на 0.04 (нормализованные координаты), если значение в ckpt некорректное (≤0 или >0.25). `PlannerConfig.goal_threshold=10` — training default; в нормализованном пространстве [0,1] с max dist ≈ 1.41 это означает "дошёл на первом шаге". У нас же чекпоинт лежит как есть — если он когда-либо попадёт с битым значением, путь превратится в тривиальный `[start, goal]`.

Правка в [microservices/planner-service/app/core/planner.py](microservices/planner-service/app/core/planner.py) — после загрузки `ckpt["config"]` применяем тот же гард: если `goal_threshold <= 0 или > 0.25` — переписываем на 0.04.

**3. Harden: fail-fast на слишком большую карту.** PE в модели precomputed для 16×16 токенов (encoder downsamples 16×, поддерживает до 256×256 карт). Если map-service отправит большую карту — модель тихо обрежет PE. Правка в [microservices/planner-service/app/core/preprocessor.py](microservices/planner-service/app/core/preprocessor.py) — `raise ValueError` при `max(H, W) > 256`.

**Что НЕ баг (проверено в процессе):**
- ✓ Формулы `(u, v, safety_field)` совпадают с `pathformer/src/pathformer/data/dataset.py:70-84`
- ✓ Нормализация координат `/(H-1, W-1)` совпадает
- ✓ Порядок осей `(row, col)` и каналов `[u, v, safety_field]`
- ✓ `max_current_global=3.0` совпадает с `MapConfig.max_intensity=3.0` (дефолт генератора)
- ✓ Размер карты 128×128 — training-default из `generate_dataset.py:46`, encoder поддерживает до 256×256
- ✓ `vessel_max_current / max_current_global` — исправлено в Wave 5

**Ожидаемый эффект:** при повторном запросе с тем же `seed=42` и `start=[10,10], goal=[120,120]` путь больше не должен "залипать" возле острова — A* автоматически проведёт его через проход. В JSON-ответе будет видно `"n_repairs": N`, где N > 0 если модель "промахнулась" по препятствиям.

## Общие решения

**Координаты в API:** пиксели `(row, col)`. Для карты 128×128 — `start=[10,10]`, `goal=[120,120]`. Внутри нормализуются в `[0, 1]` перед подачей в модель.

**Веса `best.pt`:** монтируются volume'ом из `./pathformer/best.pt` в контейнер на `/app/weights/best.pt:ro` — не копируются в образ, можно подменить без пересборки.

**RabbitMQ exchange:** `pathformer`, topic. Routing keys: `map.created`, `plan.completed`. Сообщения persistent, с publisher confirms.

**Обработка ошибок:** map_id не найден → HTTP 404. Остальные — 500. MinIO `ensure_bucket` — idempotent head → create.

**Что не реализовано (осознанно):**
- Subscriber для событий (планнер мог бы подписаться на `map.created` для прогрева). Достаточно publish-only для MVP.
- Auth/rate limit — локальный MVP.
- Tests — пока нет, код разделён по слоям так что добавить unit-тесты на use-case'ы с fake-портами тривиально.
