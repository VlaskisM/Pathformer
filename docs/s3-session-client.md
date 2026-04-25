# S3 / aioboto3: Session, Client и пул соединений

Заметка про то, как устроена работа с S3 через `aioboto3` — что такое `Session`, что такое `Client`, зачем нужен контекстный менеджер и где на самом деле открывается пул соединений.

---

## 1. Главная идея

В `aioboto3` (и в `boto3`) есть **три уровня абстракции**:

| Уровень | Что это | Что внутри |
|---|---|---|
| `Session` | Конфиг с креденшелами | Просто данные. Соединений нет. |
| Контекстный менеджер (`session.client(...)`) | "Заготовка клиента" | Знает, как открыть клиента. Но ещё не открыл. |
| `Client` | Рабочий объект | Имеет HTTP пул соединений, методы `put_object`, `get_object` и т.д. |

Между ними **строгий порядок**: `Session` → создаёт `ContextManager` → открывается в `Client`.

---

## 2. Session — это просто конфиг

```python
session = aioboto3.Session()
```

Что внутри `Session`:
- access_key / secret_key
- region
- profile

Что **НЕ** внутри `Session`:
- ❌ HTTP-соединений
- ❌ пула соединений
- ❌ открытых сокетов
- ❌ знания о конкретном сервисе AWS (S3, SQS, ...)

**Session ничего не открывает.** Это объект-конфиг в памяти. Создал — и забыл, он бесплатный.

### Аналогия

`Session` ≈ строка подключения к БД (`postgresql://user:pass@host/db`). Сама по себе она не открывает соединение — это просто текст с настройками.

---

## 3. session.client(...) — это НЕ клиент

```python
ctx = session.client(
    "s3",
    endpoint_url="http://minio:9000",
    aws_access_key_id="...",
    aws_secret_access_key="...",
)
```

Это **фабричный метод**, который возвращает **асинхронный контекстный менеджер**.

> ⚠️ Важно: `ctx` — это **не клиент**. Это "запечатанная коробка", которая умеет открыть клиента.

Что произошло после этой строки:
- ✅ Создан объект-обёртка
- ✅ Он знает, к какому сервису (`s3`) и куда подключаться
- ❌ Ни одного TCP-соединения ещё не открыто
- ❌ Никакого пула ещё нет

### Зачем такое разделение

Из одной `Session` можно создать **несколько разных клиентов** под разные сервисы:

```python
session = aioboto3.Session()  # один конфиг с кредами

s3_ctx  = session.client("s3",  endpoint_url="...")  # заготовка к S3
sqs_ctx = session.client("sqs", endpoint_url="...")  # заготовка к SQS
```

Креденшелы те же, но клиенты — разные, со своими пулами.

---

## 4. __aenter__ — здесь открывается пул

```python
client = await ctx.__aenter__()
```

**Вот тут** наконец-то происходит реальная работа:

1. Создаётся `aiohttp.TCPConnector` — это и есть пул соединений
2. Открывается HTTP-сессия к endpoint'у S3
3. Возвращается рабочий объект `client` с методами

Теперь `client` умеет:

```python
await client.put_object(Bucket=..., Key=..., Body=...)
await client.get_object(Bucket=..., Key=...)
await client.head_bucket(Bucket=...)
```

Каждый вызов **берёт свободное соединение из пула**, делает HTTP-запрос, возвращает соединение обратно. Параллельные вызовы идут по разным соединениям одновременно.

### Аналогия

`Client` ≈ `Engine` в SQLAlchemy с уже открытым пулом — реальные сокеты, готовые к работе.

---

## 5. __aexit__ — закрывает пул

```python
await ctx.__aexit__(None, None, None)
```

- Закрывает все соединения в пуле
- Освобождает сокеты
- После этого `client` использовать **нельзя**

Закрыть клиента можно **только через тот же контекстный менеджер**, который его создал. Поэтому в коде хранятся **обе** ссылки — `_ctx` и `_client`.

---

## 6. Эквивалент через async with

В обычном коде это пишут просто:

```python
async with session.client("s3", ...) as client:
    await client.put_object(...)
# здесь автоматически закрылось
```

Это ровно то же самое, что:

```python
ctx = session.client("s3", ...)
client = await ctx.__aenter__()
try:
    await client.put_object(...)
finally:
    await ctx.__aexit__(None, None, None)
```

### Почему в нашем коде вручную, а не через async with

Мы используем FastAPI **lifespan** — открытие в `connect()`, закрытие в `close()`. Эти функции вызываются в разных местах жизненного цикла приложения, и обернуть их одним `async with` нельзя. Поэтому приходится вручную звать `__aenter__` / `__aexit__` и хранить ссылку на `_ctx`.

---

## 7. Полный жизненный цикл

```
Session                  ← объект-конфиг. Ничего не открыто.
   │
   │ .client("s3", ...)
   ▼
ContextManager (_ctx)    ← "заготовка". Ещё ничего не открыто.
   │
   │ __aenter__()
   ▼
Client + Pool (_client)  ← ОТКРЫЛСЯ пул соединений. Можно работать.
   │
   │ put_object / get_object / ...
   │ (берут соединения из пула)
   │
   │ __aexit__()
   ▼
Закрыто                  ← пул закрыт, сокеты освобождены.
```

---

## 8. Чем отличается от SQLAlchemy

В SQLAlchemy сессия — это **единица работы со своим состоянием**: identity map, отслеживание изменений, транзакция. Поэтому новая сессия на каждый запрос — это про **изоляцию состояния**.

В S3 **никакого состояния нет**. Каждый HTTP-запрос полностью самодостаточен (stateless). `Session` в `aioboto3` — это **не "единица работы"**, а **просто конфиг**.

| | SQLAlchemy `Session` | aioboto3 `Session` |
|---|---|---|
| Хранит состояние | ✅ identity map, dirty objects | ❌ только креды |
| Транзакция | ✅ | ❌ (S3 stateless) |
| Создавать на каждый запрос | ✅ да | ❌ один на приложение |
| Где живёт пул соединений | в `Engine` | в `Client` |

**Вывод:** в `aioboto3` `Session` создаётся **один раз на всё приложение**. На каждый запрос новую сессию делать **не нужно** и не имеет смысла.

---

## 9. Где живут соединения

```
Session       — пусто (только настройки)
   │
   ▼
Client        — здесь живёт пул соединений (aiohttp.TCPConnector)
   │           каждый put_object/get_object берёт соединение из пула
   ▼
HTTP requests — параллельно идут по разным TCP-соединениям
```

При параллельной заливке (`asyncio.gather`) запросы идут одновременно по разным соединениям из пула — клиент не блокирует сам себя.

---

## 10. Применимо к нашему коду

В [map-service/src/adapters/storage.py](../src/adapters/storage.py):

```python
class S3Client(S3ClientInterface):
    def __init__(self, endpoint, access_key, secret_key):
        self._session = aioboto3.Session()  # ← конфиг, ничего не открыто
        self._client = None
        self._ctx = None

    async def connect(self):
        self._ctx = self._session.client("s3", ...)         # ← заготовка
        self._client = await self._ctx.__aenter__()         # ← пул открылся

    async def upload(self, bucket, key, data):
        await self._client.put_object(Bucket=bucket, ...)   # ← берём из пула

    async def close(self):
        await self._ctx.__aexit__(None, None, None)         # ← пул закрыт
```

И в lifespan:

```python
storage = S3Client(...)         # __init__ — Session создан, всё
await storage.connect()         # __aenter__ — пул открылся
# ... приложение работает, использует storage ...
await storage.close()           # __aexit__ — пул закрыт
```

---

## Полезные ссылки

- [aioboto3 docs](https://aioboto3.readthedocs.io/en/latest/usage.html)
- [boto3 Session reference](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/core/session.html)
- [boto3 credentials guide](https://boto3.amazonaws.com/v1/documentation/api/latest/guide/credentials.html)
- [aiohttp connectors / pooling](https://docs.aiohttp.org/en/stable/client_advanced.html#connectors)
- [S3 REST API](https://docs.aws.amazon.com/AmazonS3/latest/API/Welcome.html)
