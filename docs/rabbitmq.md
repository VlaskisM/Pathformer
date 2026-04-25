# RabbitMQ: полная теория

Заметка про то, как устроен RabbitMQ — что такое exchange, queue, binding, как сообщения попадают от паблишера к консумеру, и почему "просто кинуть в очередь" работает не так, как кажется.

---

## 1. Зачем вообще нужен брокер сообщений

Когда два сервиса общаются через HTTP — это **синхронный** разговор. Клиент шлёт запрос и **ждёт ответа**. Если получатель упал — у клиента ошибка.

Брокер сообщений (RabbitMQ, Kafka, NATS, ...) ставится **между** сервисами:

```
HTTP-стиль:
[A] ────HTTP request───→ [B]
[A] ←───HTTP response─── [B]
   (если B упал — ошибка)

Брокер:
[A] ──→ [Broker] ──→ [B]
       (хранит)    (читает когда хочет)
```

Что это даёт:
- **Развязка во времени**: A пишет, B читает потом — даже если B был выключен
- **Развязка в адресах**: A не знает про B, оба знают только про брокер
- **Развязка в количестве**: одно сообщение от A может прочитать 0, 1 или много B
- **Гарантии доставки**: брокер запоминает сообщение и не теряет его, пока кто-то не подтвердит обработку

RabbitMQ — это реализация **AMQP 0-9-1** (протокол), один из самых популярных брокеров.

---

## 2. Главные понятия (3-минутная карта)

```
   Producer                  RabbitMQ                   Consumer
   ───────                ────────────                 ────────
   publish(msg) ─────→    Exchange ──binding──→ Queue ─────→ msg
                          (роутер)              (буфер)
```

| Сущность | Что это | Аналогия |
|---|---|---|
| **Connection** | TCP-соединение к RabbitMQ | сокет |
| **Channel** | Виртуальное соединение поверх Connection | "вкладка" в TCP |
| **Exchange** | Маршрутизатор, решает куда положить сообщение | почтовое отделение |
| **Queue** | Буфер, где сообщения ждут consumer'а | почтовый ящик |
| **Binding** | Правило "exchange ↔ queue" | подписка на рассылку |
| **Routing key** | Метка на сообщении для маршрутизации | адрес на конверте |
| **Producer** | Тот, кто публикует | отправитель |
| **Consumer** | Тот, кто читает | получатель |

**Ключевое**: producer **никогда не пишет в queue напрямую**. Только в exchange. Exchange по правилам binding'ов раскладывает сообщения по queue'ам.

---

## 3. Connection и Channel

### Connection
Это TCP-соединение к RabbitMQ. Дорогое в установке (handshake, авторизация), поэтому **держится открытым** всё время жизни приложения.

```python
self._connection = await aio_pika.connect_robust("amqp://guest:guest@rabbitmq:5672/")
```

`connect_robust` означает "автоматически переподключайся, если связь оборвётся".

### Channel
Это **виртуальное соединение** внутри Connection. Дешёвое — можно создавать сколько угодно. Каждый channel — независимый поток операций (publish, declare, consume).

```python
self._channel = await self._connection.channel(publisher_confirms=True)
```

**Почему два уровня?**
- TCP-соединений хочется иметь мало (дороги)
- Но операций параллельных хочется много (publish из разных корутин)
- Channel — это "лёгкий поток" поверх одного TCP

**Правила хорошего тона:**
- Один Connection на приложение
- Один Channel **на поток / корутину** (channel'ы не thread-safe!)
- Никогда не использовать один channel из двух потоков одновременно

---

## 4. Exchange: маршрутизатор

Exchange — это **роутер**. Producer публикует сообщение в exchange с **routing key**, exchange решает, в какие queue его положить (или выкинуть, если некуда).

### Типы exchange'ей

| Тип | Логика маршрутизации | Когда использовать |
|---|---|---|
| **direct** | routing key должен **точно совпасть** с binding key | "отправь именно очереди X" |
| **topic** | routing key матчится паттернам с `*` и `#` | "все события вида user.*" |
| **fanout** | broadcast в **все** привязанные queue, routing key игнорируется | "всем подписчикам" |
| **headers** | матчинг по **заголовкам сообщения**, а не routing key | редко используется |

### Direct exchange

```
publish(routing_key="orders")
                  │
                  ▼
              ┌─────────┐
              │ exchange│
              └────┬────┘
        binding="orders" │
                  ▼
              ┌─────────┐
              │ queue_A │
              └─────────┘
```

Если binding key очереди = `"orders"` и routing key сообщения = `"orders"` — сообщение попадает в queue. Иначе — нет.

### Topic exchange (наш случай)

В планнере используется topic:
```python
await self._channel.declare_exchange(
    self._exchange_name,
    aio_pika.ExchangeType.TOPIC,
    durable=True,
)
```

Routing key — это строка с точками: `"map.created"`, `"plan.completed"`, `"user.123.login"`.

Binding key поддерживает wildcards:
- `*` — ровно одно слово (`map.*` матчит `map.created`, но не `map.x.y`)
- `#` — ноль или больше слов (`map.#` матчит `map`, `map.created`, `map.x.y.z`)

Примеры:
```
binding="map.created"  → только map.created
binding="map.*"        → map.created, map.deleted (одно слово)
binding="map.#"        → всё, что начинается с map.
binding="#"            → всё подряд
```

### Fanout — для broadcast

Игнорирует routing key вообще, шлёт во все привязанные очереди. Удобно для "рассылка всем подписчикам" (логи, метрики).

---

## 5. Queue: буфер

Queue — это **очередь сообщений в памяти/на диске** RabbitMQ. Сообщение лежит в queue, пока его не заберёт consumer.

### Свойства queue

```python
await channel.declare_queue(
    "my_queue",
    durable=True,        # переживает рестарт RabbitMQ
    auto_delete=False,   # удалить, когда последний consumer отвалится
    exclusive=False,     # доступна только одному connection
)
```

| Флаг | Что значит |
|---|---|
| `durable=True` | Метаданные queue сохраняются на диск. После рестарта брокера queue остаётся. |
| `auto_delete=True` | Удалить, когда последний consumer отвалится. Для temporary queue. |
| `exclusive=True` | Видна только тому connection, который её создал. Удаляется при разрыве. |

### Persistent vs durable — путаница

Это две **разные вещи**:
- **Durable queue** — сохраняются метаданные queue (она существует после рестарта)
- **Persistent message** (`delivery_mode=2`) — сохраняется **сообщение** на диск

Чтобы сообщение пережило рестарт брокера, нужны **оба флага**:
```python
# В нашем коде так и сделано:
message = aio_pika.Message(
    body=...,
    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,  # сообщение на диск
)
queue = channel.declare_queue("X", durable=True)     # очередь на диск
```

Без любого из них — потеряешь сообщения при падении брокера.

---

## 6. Binding: подписка queue на exchange

Binding — это **правило "копировать сообщения из exchange в queue"**.

```python
queue = await channel.declare_queue("orders_handler", durable=True)
await queue.bind(exchange, routing_key="orders.*")
```

Теперь любое сообщение, опубликованное в `exchange` с routing key вида `orders.created`, `orders.updated` — окажется в `orders_handler`.

Одна queue может быть привязана к нескольким exchange'ам и иметь несколько binding'ов с разными ключами.

---

## 7. Полный путь сообщения

```
Producer:
   channel.publish(message, routing_key="map.created")
                        │
                        ▼
   ┌─────────────────────────────────────────────┐
   │              Exchange "pathformer"           │
   │                  (type: topic)               │
   └───────┬──────────────────┬───────────────────┘
           │ binding=          │ binding=
           │ "map.created"     │ "map.#"
           ▼                   ▼
   ┌─────────────┐       ┌─────────────┐
   │ Queue "plan"│       │Queue "audit"│
   └──────┬──────┘       └──────┬──────┘
          │                     │
          ▼                     ▼
   planner-service        audit-service
   consumer               consumer
```

**Важно**: одно сообщение **дублируется** во все подходящие queue. Если три queue матчатся — три копии. Каждый consumer обрабатывает свою копию независимо.

---

## 8. Acknowledgements (ack / nack)

Когда consumer получает сообщение, он должен **подтвердить** его обработку:

```python
async with queue.iterator() as q:
    async for message in q:
        async with message.process():  # auto-ack on success, nack on exception
            await handle(message.body)
```

| Действие | Что значит |
|---|---|
| **ack** | "обработал, удаляй из queue" |
| **nack** (или reject) | "не смог, верни в queue или выкини" |
| **timeout без ack** | сообщение возвращается в queue (consumer считается мёртвым) |

Без этого механизма потерял consumer связь — потерялось сообщение.

### auto-ack vs manual-ack

- **auto-ack=True** — сообщение помечается обработанным **сразу при получении**. Если consumer упадёт во время обработки — сообщение потеряно.
- **auto-ack=False** (по умолчанию в нормальных клиентах) — consumer сам решает, когда ack'ать. Безопасно.

**Правило**: всегда `auto_ack=False`. Делать ack только после успешной обработки.

---

## 9. Publisher confirms

По умолчанию publish — это "fire and forget". Producer бросил сообщение в TCP — и забыл. Если RabbitMQ упал по дороге — сообщение потерялось.

**Publisher confirms** — это режим, где брокер **подтверждает каждое сообщение**:

```python
self._channel = await self._connection.channel(publisher_confirms=True)
await exchange.publish(message, routing_key="...")
# await вернётся, только когда брокер подтвердит запись
```

С confirms publish **в десятки раз медленнее**, но гарантирует что сообщение долетело.

В нашем коде [broker.py:30](planner-service/src/adapters/broker.py#L30) включён.

---

## 10. Prefetch / QoS

Если у consumer'а медленная обработка, а сообщений много — RabbitMQ начнёт **запихивать** в него сообщения пачками. Они будут лежать у consumer'а в памяти, не ack'нутые.

Prefetch ограничивает: "не давай мне больше N не-ack'нутых сообщений".

```python
await channel.set_qos(prefetch_count=10)
```

Без этого — risk OOM на медленных consumer'ах.

---

## 11. Dead Letter Exchange (DLX)

Что делать с сообщениями, которые **никак не обрабатываются** (consumer всегда крашится на них)?

DLX — специальный exchange, куда автоматически попадают сообщения, которые:
- nack'нули с `requeue=False`
- TTL истёк
- очередь переполнена

```python
queue = await channel.declare_queue(
    "main",
    arguments={
        "x-dead-letter-exchange": "dlx",
        "x-dead-letter-routing-key": "main.failed",
    },
)
```

Удобно для дебага — все "битые" сообщения копятся в одной DLQ для разбора.

---

## 12. Применимо к нашему коду

### Что есть

В [planner-service/src/adapters/broker.py](planner-service/src/adapters/broker.py):

```python
await self._channel.declare_exchange(
    self._exchange_name,           # "pathformer"
    aio_pika.ExchangeType.TOPIC,    # topic — routing keys с точками
    durable=True,                    # переживёт рестарт
)
```

И публикация:
```python
message = aio_pika.Message(
    body=json.dumps(payload).encode("utf-8"),
    delivery_mode=aio_pika.DeliveryMode.PERSISTENT,  # на диск
)
await self._exchange.publish(message, routing_key=routing_key)
```

### Что отсутствует

```
                                Exchange "pathformer"
publish "map.created"   ───→    (topic, durable)         ❌ нет binding'ов
                                                          ❌ нет queue
                                                          ❌ нет consumer'а
```

**События публикуются, но никто их не слушает.** Сообщения попадают в exchange, exchange не находит подходящих queue по routing key и **выбрасывает** их (если флаг `mandatory=False`, что по умолчанию).

Чтобы события действительно работали:

1. **Создать queue** где-то (в consumer-сервисе):
   ```python
   queue = await channel.declare_queue("planner_inbox", durable=True)
   await queue.bind(exchange, routing_key="map.created")
   ```

2. **Слушать её**:
   ```python
   async with queue.iterator() as q:
       async for message in q:
           async with message.process():
               event = json.loads(message.body)
               await handle_map_created(event)
   ```

---

## 13. Полезные команды

### Management UI
RabbitMQ из коробки имеет веб-интерфейс на порту **15672**:

```
http://localhost:15672
login: guest / guest
```

Там можно:
- посмотреть exchange'ы и их типы
- посмотреть queue'ы и их размер
- увидеть binding'и
- руками опубликовать тестовое сообщение
- посмотреть содержимое queue (Get messages)

### Что проверить вживую

```bash
# Сколько сообщений в каждой queue
rabbitmqctl list_queues name messages messages_ready messages_unacknowledged

# Какие binding'и
rabbitmqctl list_bindings

# Состояние exchange'ей
rabbitmqctl list_exchanges
```

---

## 14. Типичные паттерны

### Work queue (worker pool)
Один publisher, много consumer'ов на одной queue. Каждое сообщение обрабатывает **только один** consumer (round-robin). Используется для распараллеливания работы.

```
publisher ──→ exchange ──→ queue ──┬──→ worker 1
                                   ├──→ worker 2
                                   └──→ worker 3
```

### Pub/Sub (fanout)
Один publisher, много queue'ов с независимыми consumer'ами. Каждый получает **свою копию** каждого сообщения.

```
publisher ──→ exchange ──┬──→ queue_A ──→ consumer A
                         ├──→ queue_B ──→ consumer B
                         └──→ queue_C ──→ consumer C
```

### Routing
Topic exchange + binding'и с wildcards. Разные consumer'ы подписываются на разные подмножества событий.

```
publisher ──→ exchange (topic) ──┬─[map.*]──→ queue_planner
                                 └─[#]──────→ queue_audit (ловит всё)
```

### RPC over RabbitMQ
Запрос-ответ через два exchange'а. Используется редко (HTTP лучше для RPC).

---

## 15. Cheat sheet

```python
# 1. Connection (один на приложение)
connection = await aio_pika.connect_robust("amqp://user:pass@host:5672/")

# 2. Channel (по одному на корутину/поток)
channel = await connection.channel(publisher_confirms=True)

# 3. Exchange (декларируется на старте)
exchange = await channel.declare_exchange(
    "my_exchange",
    aio_pika.ExchangeType.TOPIC,
    durable=True,
)

# 4. Queue (декларируется consumer'ом)
queue = await channel.declare_queue("my_queue", durable=True)
await queue.bind(exchange, routing_key="events.*")

# 5. Publish
await exchange.publish(
    aio_pika.Message(
        body=b"hello",
        content_type="application/json",
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    ),
    routing_key="events.created",
)

# 6. Consume
async with queue.iterator() as q:
    async for message in q:
        async with message.process():  # auto-ack on success
            print(message.body)

# 7. Close
await connection.close()
```

---

## 16. Краткие правила хорошего тона

1. **Один Connection на приложение.** Не открывай новый на каждый publish.
2. **Channel не thread-safe.** Не дели его между потоками/корутинами.
3. **Всегда `durable=True` для queue + `PERSISTENT` для message** — иначе потеряешь данные при рестарте.
4. **Всегда `auto_ack=False`** — иначе потеряешь сообщения при падении consumer'а.
5. **Используй `connect_robust`** — соединение само переподключится при сбоях.
6. **Включай `publisher_confirms`** для критичных событий — гарантирует доставку до брокера.
7. **Ставь `prefetch_count`** для consumer'ов — иначе OOM на медленной обработке.
8. **Routing keys через точку**: `entity.action` (`map.created`, `user.deleted`) — стандарт.
9. **Не забывай про DLQ** для production — сообщения, которые не получается обработать, должны куда-то деваться.
10. **Декларируй exchange и queue идемпотентно** — declare с теми же параметрами безопасно вызывать повторно.

---

## Полезные ссылки

- [Официальные туториалы RabbitMQ (с Python)](https://www.rabbitmq.com/tutorials)
- [AMQP 0-9-1 Concepts](https://www.rabbitmq.com/tutorials/amqp-concepts)
- [aio-pika docs](https://aio-pika.readthedocs.io/)
- [pika (sync клиент)](https://pika.readthedocs.io/) — на случай, если попадётся синхронный код
- [RabbitMQ in Depth (Roy Hashimoto)](https://www.manning.com/books/rabbitmq-in-depth) — лучшая книга
