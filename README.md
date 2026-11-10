# WatchTrack — трекер фільмів і серіалів

Курс «Інженерія високонавантажених систем». **Лабораторна робота №1:** системне проєктування та базовий прототип.

WatchTrack — вебсервіс, де користувач веде облік переглянутого: які фільми й серіали дивиться, на якій серії зупинився, що планує. Він ставить оцінки, бачить свою статистику, а на головній показуються тренди.

**Стек:** Python 3.13 · FastAPI · SQLAlchemy 2 (async) + asyncpg · PostgreSQL 16 · Alembic · Docker Compose

---

## Зміст
1. [Швидкий старт](#1-швидкий-старт)
2. [Доменний аналіз](#2-доменний-аналіз)
3. [Специфікація API](#3-специфікація-api)
4. [Піковий сценарій навантаження](#4-піковий-сценарій-навантаження-high-load-scenario)
5. [Архітектура](#5-архітектура)
6. [Аналіз вузьких місць](#6-аналіз-вузьких-місць-bottleneck-analysis)
7. [Структура репозиторію](#7-структура-репозиторію)
8. [Розподіл відповідальності (RACI)](#8-розподіл-відповідальності-raci)

---

## 1. Швидкий старт

Потрібен лише Docker (з Docker Compose v2).

```bash
git clone <url-репозиторію>
cd watchtrack
docker compose up --build -d
```

Одна команда робить усе:
1. піднімає PostgreSQL;
2. чекає, поки БД стане healthy;
3. застосовує міграції (`alembic upgrade head`);
4. заповнює демо-дані: 10 тайтлів, 3 користувачі, історію переглядів;
5. запускає API.

| Що | Адреса |
|---|---|
| Swagger UI (інтерактивна документація) | http://localhost:8000/docs |
| OpenAPI JSON | http://localhost:8000/openapi.json |
| Health-check | http://localhost:8000/health |
| PostgreSQL (для DBeaver/psql) | `localhost:5433`, user/pass/db: `watchtrack` |

Порти та паролі змінюються через `.env` (зразок у `.env.example`).

### Перевірка збереження даних (persistence)

```bash
# 1. Створити дані
curl -X POST localhost:8000/api/v1/users -H "Content-Type: application/json" \
     -d '{"username":"demo","email":"demo@example.com"}'

# 2. Перезапустити контейнер БД (або повністю видалити контейнери — volume залишиться)
docker compose restart db
# або: docker compose down && docker compose up -d

# 3. Дані на місці
curl localhost:8000/api/v1/users/4
```

Дані лежать у named volume `pgdata`. Повне очищення виконується лише явно: `docker compose down -v`.

### Корисні команди

```bash
docker compose logs -f api                              # логи API
docker compose exec db psql -U watchtrack               # консоль БД
docker compose exec api alembic current                 # поточна версія схеми
docker compose down -v && docker compose up --build -d  # чистий старт з нуля
```

### Запуск без Docker (для розробки)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
docker compose up -d db                             # лише БД
export DATABASE_URL=postgresql+asyncpg://watchtrack:watchtrack@localhost:5433/watchtrack
alembic upgrade head && python -m scripts.seed
uvicorn app.main:app --reload
ruff check . && ruff format --check .               # перевірка стилю коду
```

---

## 2. Доменний аналіз

### Сутності

| Сутність | Таблиця | Опис | Ключові поля |
|---|---|---|---|
| **User** | `users` | Користувач сервісу | `username` (унікальний), `email` (унікальний) |
| **Title** | `titles` | Фільм або серіал | `type` (movie/series), `name`, `year`, `runtime_minutes`, `genres[]` |
| **Season** | `seasons` | Сезон серіалу | `title_id`, `number` (0 — спецвипуски) |
| **Episode** | `episodes` | Серія сезону | `season_id`, `number`, `air_date`, `runtime_minutes` |
| **UserTitle** | `user_titles` | Тайтл у списку користувача | PK (`user_id`, `title_id`), `status`, `rating` 1–10 |
| **EpisodeWatch** | `episode_watches` | Факт перегляду серії | PK (`user_id`, `episode_id`), `watched_at` |

### ER-діаграма

```mermaid
erDiagram
    USERS ||--o{ USER_TITLES : "веде список"
    TITLES ||--o{ USER_TITLES : "є у списках"
    TITLES ||--o{ SEASONS : "має"
    SEASONS ||--o{ EPISODES : "містить"
    USERS ||--o{ EPISODE_WATCHES : "переглянув"
    EPISODES ||--o{ EPISODE_WATCHES : "переглянута"

    USERS {
        int id PK
        varchar username UK
        varchar email UK
        varchar display_name
        timestamptz created_at
    }
    TITLES {
        int id PK
        enum type "movie | series"
        varchar name
        varchar original_name
        smallint year
        text description
        smallint runtime_minutes
        varchar_array genres "GIN-індекс"
    }
    SEASONS {
        int id PK
        int title_id FK
        smallint number "UNIQUE(title_id, number)"
        date release_date
    }
    EPISODES {
        int id PK
        int season_id FK
        smallint number "UNIQUE(season_id, number)"
        date air_date
        smallint runtime_minutes
    }
    USER_TITLES {
        int user_id PK,FK
        int title_id PK,FK
        enum status "planned | watching | completed | dropped"
        smallint rating "CHECK 1..10"
        timestamptz updated_at
    }
    EPISODE_WATCHES {
        int user_id PK,FK
        int episode_id PK,FK
        timestamptz watched_at
    }
```

### Бізнес-правила

1. **Автоматичний статус.** Перша відмічена серія переводить тайтл у `watching` (і додає його у список, якщо його там не було). Відмітка останньої серії дає `completed`. Зняття відмітки з завершеного серіалу повертає `watching`.
2. **Ідемпотентність відміток.** Повторна відмітка серії нічого не дублює: складений PK + `INSERT ... ON CONFLICT DO NOTHING`. Відповідь `201`, якщо серію відмічено вперше, і `200`, якщо вона вже була відмічена.
3. **Оцінку не можна поставити тайтлу зі статусом `planned`**: не можна оцінити те, що ще не дивився.
4. **Сезони є тільки в серіалів.** Фільму не можна додати сезон.
5. **Унікальність:** номер сезону в межах серіалу та номер серії в межах сезону.
6. **Каскадне видалення.** Видалення тайтлу прибирає його сезони, серії, відмітки й записи у списках (`ON DELETE CASCADE` на рівні БД).
7. **Узгодженість під конкурентним доступом.** Зміни прогресу одного користувача по одному тайтлу серіалізуються транзакційним advisory-lock. Без нього дві паралельні відмітки двох останніх серій не виставили б `completed` (аномалія write skew).

---

## 3. Специфікація API

Повна інтерактивна специфікація: **Swagger UI** за адресою `/docs` після запуску. Усі бізнес-ендпоінти мають префікс `/api/v1`.

### Каталог

| Метод | Шлях | Опис | Коди |
|---|---|---|---|
| `POST` | `/titles` | Додати фільм/серіал разом із сезонами та серіями | 201, 422 |
| `GET` | `/titles` | Каталог: фільтри `type`, `genre`, `year`, пошук `q`, сортування `sort`, пагінація `limit`/`offset` | 200, 422 |
| `GET` | `/titles/{id}` | Сторінка тайтлу: сезони, середня оцінка, кількість глядачів | 200, 404 |
| `PATCH` | `/titles/{id}` | Часткове оновлення | 200, 404, 422 |
| `DELETE` | `/titles/{id}` | Видалення (каскадно) | 204, 404 |
| `POST` | `/titles/{id}/seasons` | Додати сезон | 201, 404, 409, 422 |
| `GET` | `/titles/{id}/seasons/{n}` | Сезон зі списком серій | 200, 404 |
| `POST` | `/titles/{id}/seasons/{n}/episodes` | Вийшла нова серія | 201, 404, 409 |
| `GET` | `/trending` | Популярне за N днів (`days`, `limit`, `type`) | 200 |

### Користувачі та трекінг

| Метод | Шлях | Опис | Коди |
|---|---|---|---|
| `POST` | `/users` | Реєстрація | 201, 409, 422 |
| `GET` | `/users/{id}` | Профіль | 200, 404 |
| `GET` | `/users/{id}/stats` | Статистика: години, серії, розподіл за статусами | 200, 404 |
| `GET` | `/users/{id}/titles` | Списки користувача (`status`, пагінація) | 200, 404 |
| `PUT` | `/users/{id}/titles/{title_id}` | Встановити статус і оцінку (upsert) | 201 / 200, 404, 422 |
| `DELETE` | `/users/{id}/titles/{title_id}` | Прибрати зі списку разом із відмітками | 204, 404 |
| `GET` | `/users/{id}/titles/{title_id}/progress` | Прогрес і наступна серія | 200, 404 |
| `POST` | `/users/{id}/episodes/{episode_id}/watch` | Відмітити серію (ідемпотентно) | 201 / 200, 404 |
| `DELETE` | `/users/{id}/episodes/{episode_id}/watch` | Зняти відмітку | 200, 404 |
| `POST` | `/users/{id}/seasons/{season_id}/watch` | Відмітити весь сезон | 201 / 200, 404 |

Службовий ендпоінт `GET /health` перевіряє з'єднання з БД і повертає `200` або `503`.

### Формат помилок

Усі помилки мають однакову структуру:

```json
{ "error": { "code": "not_found", "message": "Title 999 not found" } }
```

| HTTP | `code` | Коли |
|---|---|---|
| 404 | `not_found` | Ресурсу не існує |
| 409 | `conflict` | Дублікат (username, номер сезону/серії) |
| 422 | `validation_error` | Невалідне тіло чи параметри (поле `details` з переліком помилок) |
| 422 | `business_rule_violation` | Порушення бізнес-правила (оцінка для `planned`, сезон для фільму) |
| 500 | `internal_error` | Непередбачена помилка (деталі тільки в логах сервера) |

### Приклади запитів для демонстрації

```bash
# Каталог: серіали жанру drama, новіші спершу
curl "localhost:8000/api/v1/titles?type=series&genre=drama&sort=-year"

# Сторінка серіалу «Пуститися берега»
curl localhost:8000/api/v1/titles/1

# Користувач 1 (alex, переглянув сезони 1–2) відмічає першу серію третього сезону → статус і прогрес оновлюються автоматично
curl -X POST localhost:8000/api/v1/users/1/episodes/21/watch

# Прогрес і наступна серія
curl localhost:8000/api/v1/users/1/titles/1/progress

# Оцінити фільм
curl -X PUT localhost:8000/api/v1/users/1/titles/7 -H "Content-Type: application/json" \
     -d '{"status":"completed","rating":10}'

# Порушення бізнес-правила → 422
curl -X PUT localhost:8000/api/v1/users/1/titles/8 -H "Content-Type: application/json" \
     -d '{"status":"planned","rating":7}'

# Статистика і тренди
curl localhost:8000/api/v1/users/1/stats
curl "localhost:8000/api/v1/trending?days=7&limit=5"
```

---

## 4. Піковий сценарій навантаження (High-Load Scenario)

### «Ніч прем'єри»

О 04:00 виходить нова серія популярного серіалу. Протягом першої години сотні тисяч людей дивляться її й заходять на сайт. Послідовність запитів одного користувача:

```
1. GET  /api/v1/titles/{id}                       — оновлює сторінку серіалу, чекає на нову серію
2. GET  /api/v1/titles/{id}/seasons/{n}           — відкриває сезон
3. POST /api/v1/users/{u}/episodes/{e}/watch      — відмічає серію переглянутою   ← найважча операція
4. GET  /api/v1/users/{u}/titles/{id}/progress    — дивиться прогрес
5. GET  /api/v1/trending?days=1                   — головна з трендами
```

**Оцінка навантаження:** 300 000 глядачів, з них половина відмічає серію в перші 15 хвилин.
- Запис: 150 000 / 900 с ≈ **170 відміток/с** у середньому, на піку (перші хвилини) ×5 ≈ **850/с**.
- Одна відмітка — це одна транзакція з ~8 SQL-запитами (перевірка користувача й серії, блокування, INSERT, два COUNT, оновлення статусу, пошук наступної серії). Разом ≈ **6 800 SQL-запитів/с** тільки на запис.
- Читання (кроки 1, 2, 4, 5) ≈ у 10 разів більше за запис: **~8 000 HTTP-запитів/с**, і всі вони б'ють в один і той самий тайтл.

**Чому це найресурсоємніша операція:** пікові записи в найбільшу таблицю (`episode_watches`) з fsync WAL на кожен commit. Кожен запит утримує з'єднання з пулу всю транзакцію, а паралельно йдуть важкі агрегати на читання (рейтинг тайтлу, тренди) по тих самих даних.

---

## 5. Архітектура

### Архітектурний стиль: модульний моноліт

Система — один сервіс із чітко розділеними шарами та модулями:

- **Шари:** `routers` (HTTP, валідація) → `services` (бізнес-логіка, транзакції) → `models` (ORM, персистентність).
- **Модулі домену:** каталог (`titles`), трекінг (`tracking`), аналітика (`stats`), користувачі (`users`).

**Чому так, а не мікросервіси одразу:**
- для одного розробника це найменші накладні витрати на розгортання і налагодження;
- одна БД дає справжні транзакції та FK-цілісність там, де вони критичні (відмітка + статус);
- межі модулів уже проведені, тож у наступних роботах аналітику й обробку відміток можна винести в окремі воркери через чергу без переписування решти.

**Рішення, закладені під масштабування вже зараз:**
- **Stateless API.** Стан тільки в БД, тож інстанси API можна множити за балансувальником.
- **Асинхронний I/O** (FastAPI + asyncpg): один воркер обслуговує сотні одночасних запитів, поки ті чекають на БД.
- **Пул з'єднань** з `pool_pre_ping`: автоматичне перепідключення після рестарту БД.
- **`lazy="raise"` на всіх зв'язках ORM**: неявне довантаження (джерело N+1) неможливе, кожен зв'язок завантажується явно (`selectinload`).
- **Індекси під реальні запити**, зокрема GIN на `genres` для фільтра `genres @> ARRAY[...]`.
- **Ідемпотентні операції запису**: безпечні повтори з боку клієнта.

### Діаграма контейнерів (C4, рівень Container)

Суцільні лінії — реалізовано в ЛР1. Пунктир — заплановано в наступних роботах. Усі схеми також збережені як PNG у папці `docs/` (для звіту).

```mermaid
flowchart TB
    user(["👤 Користувач<br/>[Person]<br/>Веде облік переглянутого"])

    subgraph system ["WatchTrack [Software System]"]
        api["<b>API Application</b><br/>[Container: Python 3.13, FastAPI, Uvicorn]<br/>REST API, бізнес-логіка, валідація.<br/>N воркерів, stateless"]
        db[("<b>Database</b><br/>[Container: PostgreSQL 16]<br/>Користувачі, каталог, списки,<br/>відмітки переглядів")]
        migr["<b>Migrations</b><br/>[Alembic, при старті контейнера]<br/>Версіонування схеми БД"]

        lb["Load Balancer<br/>[Nginx]"]:::planned
        cache[("Cache<br/>[Redis]<br/>сторінки тайтлів, тренди")]:::planned
        mq[["Message Broker<br/>[RabbitMQ]<br/>події переглядів"]]:::planned
        worker["Workers<br/>агрегати, тренди, сповіщення"]:::planned
    end

    tmdb["TMDB API<br/>[External System]<br/>каталог фільмів і серіалів"]:::planned

    user -- "HTTPS / JSON (REST)" --> api
    api -- "SQL / TCP 5432<br/>asyncpg, пул з'єднань" --> db
    migr -- "DDL" --> db

    user -.-> lb -.-> api
    api -.->|"RESP / TCP 6379"| cache
    api -.->|"AMQP / TCP 5672"| mq
    mq -.-> worker
    worker -.-> db
    worker -.->|"HTTPS"| tmdb

    classDef planned stroke-dasharray: 5 5,opacity:0.6
```

### Діаграма компонентів API (C4, рівень Component)

```mermaid
flowchart TB
    client(["HTTP-клієнт"])

    subgraph api ["API Application (FastAPI)"]
        mw["Middleware<br/>час обробки X-Process-Time-Ms"]

        subgraph routers ["Routers — HTTP-шар: маршрути, статус-коди"]
            direction LR
            r_users["users"] ~~~ r_titles["titles<br/>каталог, тренди"] ~~~ r_tracking["tracking<br/>списки, відмітки"] ~~~ r_health["health"]
        end

        subgraph services ["Services — бізнес-логіка, транзакції"]
            direction LR
            s_users["users"] ~~~ s_titles["titles"] ~~~ s_tracking["tracking<br/>статуси, прогрес,<br/>advisory-lock"] ~~~ s_stats["stats<br/>статистика, тренди"]
        end

        schemas["Schemas (Pydantic)<br/>валідація вводу/виводу"]
        eh["Exception Handlers<br/>єдиний формат помилок"]
        orm["Persistence: SQLAlchemy ORM<br/>Models + AsyncSession + пул з'єднань"]
    end

    db[("PostgreSQL")]

    client -- "HTTP/JSON" --> mw --> routers
    routers -- "валідація" --> schemas
    routers -- "виклик" --> services
    routers -. "винятки" .-> eh
    services --> orm
    orm -- "asyncpg" --> db
```

### Потік даних «відмітити серію» (sequence)

```mermaid
sequenceDiagram
    autonumber
    participant C as Клієнт
    participant R as Router (tracking)
    participant S as TrackingService
    participant DB as PostgreSQL

    C->>R: POST /users/1/episodes/21/watch
    R->>S: mark_episode(user=1, episode=21)
    S->>DB: SELECT user / title_id серії
    S->>DB: pg_advisory_xact_lock(user, title)
    S->>DB: INSERT episode_watches ... ON CONFLICT DO NOTHING RETURNING
    S->>DB: COUNT серій тайтлу / COUNT переглянутих
    S->>DB: UPSERT/UPDATE user_titles.status
    S->>DB: SELECT наступна непереглянута серія
    S->>DB: COMMIT (знімає блокування)
    S-->>R: WatchResult {episodes_marked, progress}
    R-->>C: 201 Created (або 200, якщо вже було відмічено)
```

---

## 6. Аналіз вузьких місць (Bottleneck Analysis)

Аналіз теоретичний, на основі спроєктованої архітектури та планів виконання запитів. Перевірка навантажувальними тестами та усунення вузьких місць — у наступних лабораторних.

### BN-1. Тренди: `GET /api/v1/trending`

| | |
|---|---|
| **Компонент / запит** | `stats.get_trending`: `UNION ALL` по `episode_watches` (JOIN `episodes` → `seasons`) і `user_titles` за N днів, потім `COUNT(DISTINCT user_id) GROUP BY title_id ORDER BY ... LIMIT` |
| **Root cause** | **CPU-bound агрегація + I/O.** Запит щоразу перераховує сирі події за весь період: при 100 000 відміток/год за 7 днів це ~17 млн рядків для JOIN, хеш-агрегації й сортування. Результат однаковий для всіх користувачів, але обчислюється заново на кожен запит. `COUNT(DISTINCT)` вимагає сортування або хеша в пам'яті, і при нестачі `work_mem` сортування йде на диск. |
| **Симптоматика** | Latency росте лінійно з обсягом подій у вікні: від мілісекунд на демо-даних до секунд на продакшн-обсязі. **p99 різко зростає** саме під час прем'єри, коли подій найбільше. CPU БД близько 100% і сповільнює *всі* інші запити. З'єднання утримуються довго, тож можливе **вичерпання пулу** (`QueuePool limit reached`). RPS ендпоінту впирається в одиниці-десятки запитів/с. |

### BN-2. Відмітка серії в «ніч прем'єри»: `POST /users/{id}/episodes/{id}/watch`

| | |
|---|---|
| **Компонент / запит** | `tracking.mark_episode`: транзакція з ~8 запитів, INSERT у `episode_watches` |
| **Root cause** | **Connection pool exhaustion + I/O wait на WAL.** Кожен запит займає з'єднання з пулу (`pool_size=10 + max_overflow=10` на воркер) на весь час транзакції з 8 мережевими round-trip до БД. При 850 запитах/с і ~15 мс на транзакцію потрібно ~13 з'єднань *постійно*, а на піках більше. До того ж PostgreSQL за замовчуванням має `max_connections=100`, що обмежує сумарну кількість воркерів × пул. Кожен COMMIT робить fsync WAL на диск. Індекс `ix_episode_watches_watched_at` монотонно зростає, тож вставки б'ють в одну «праву» сторінку B-tree (**hot page / lock contention на рівні буфера**). Advisory-lock коректно серіалізує дії *одного* користувача, але додає ще один round-trip. |
| **Симптоматика** | Ріст **p95/p99 latency** через очікування вільного з'єднання (до `pool_timeout=30s`), далі **HTTP 500** з `TimeoutError` пулу. Ріст **I/O wait** і `WALWrite` в `pg_stat_activity`. **Throughput (RPS) виходить на плато** і не росте зі збільшенням кількості клієнтів, а частка помилок росте. |

### BN-3. Сторінка популярного тайтлу: `GET /api/v1/titles/{id}`

| | |
|---|---|
| **Компонент / запит** | `titles.get_title_details`: 3 SQL-запити, включно з `AVG(rating)`, `COUNT(rating)`, `COUNT(*) FILTER (...)` по `user_titles WHERE title_id = ?` |
| **Root cause** | **Read amplification, повторне обчислення незмінних даних.** Агрегати рахуються на кожен запит. Для популярного серіалу з 500 000 записів у `user_titles` це index scan по пів мільйона рядків *на кожен перегляд сторінки*. Під час прем'єри тисячі людей одночасно оновлюють *одну й ту саму* сторінку й отримують ідентичний результат. Кешу немає. |
| **Симптоматика** | Latency сторінки **пропорційна популярності тайтлу**: найпопулярніші сторінки найповільніші. Високий CPU і `shared_buffers` hit на одних і тих самих блоках. Знижений **throughput** читання, що конкурує з BN-2 за з'єднання пулу. |

### BN-4. Пошук і пагінація каталогу: `GET /api/v1/titles?q=...&offset=...`

| | |
|---|---|
| **Компонент / запит** | `titles.list_titles`: `name ILIKE '%q%' OR original_name ILIKE '%q%'`, плюс окремий `COUNT(*)` і `OFFSET` |
| **Root cause** | **Відсутність придатного індексу.** Шаблон з `%` на початку не може використати B-tree індекс `ix_titles_name`, тож `EXPLAIN` показує **Seq Scan** по всій таблиці `titles`. Запит виконується двічі (дані + `COUNT(*)` для `total`). `OFFSET N` змушує БД прочитати й відкинути N рядків, тож глибокі сторінки повільніші за перші. |
| **Симптоматика** | Latency пошуку **росте лінійно з розміром каталогу** (сотні тисяч тайтлів після імпорту з TMDB — сотні мс). Ріст CPU. Аномально повільні глибокі сторінки (`offset=5000`). Пошук «на кожне натискання клавіші» множить навантаження. |

### BN-5. Статистика активного користувача: `GET /api/v1/users/{id}/stats`

| | |
|---|---|
| **Компонент / запит** | `stats.get_user_stats`: JOIN `episode_watches → episodes → seasons → titles` по всіх переглядах користувача + ще 3 агрегати |
| **Root cause** | **Обчислення на льоту без денормалізації.** У «важкого» користувача (10 000+ переглянутих серій) кожен виклик перебирає всю його історію з трьома JOIN. Дані змінюються рідко, а читаються часто (сторінка профілю). |
| **Симптоматика** | Висока **дисперсія latency між користувачами**: p50 у нормі, а **p99 визначається найактивнішими користувачами**. Додатковий I/O на холодних даних (історія, якої немає в кеші сторінок БД). |

### Підсумок і план усунення

| # | Вузьке місце | Тип обмеження | Напрям усунення (наступні ЛР) |
|---|---|---|---|
| BN-1 | Тренди | CPU, I/O | Лічильники в Redis (sorted set по днях), кеш з TTL |
| BN-2 | Відмітка серії | Connection pool, WAL I/O | Черга подій (RabbitMQ), батч-вставки, PgBouncer, менше round-trip |
| BN-3 | Сторінка тайтлу | Read amplification | Cache-aside у Redis, денормалізовані лічильники |
| BN-4 | Пошук | Seq Scan | `pg_trgm` + GIN або OpenSearch, keyset-пагінація |
| BN-5 | Статистика | Обчислення на льоту | Інкрементальні агрегати, оновлювані воркером |

Спільна архітектурна точка відмови: **один інстанс PostgreSQL** (SPOF, немає реплік для читання). Розв'язується реплікацією в наступних роботах.

---

## 7. Структура репозиторію

```
watchtrack/
├── app/
│   ├── main.py              # FastAPI-застосунок, middleware, підключення роутерів
│   ├── config.py            # налаштування зі змінних середовища
│   ├── database.py          # async engine, пул з'єднань, сесії
│   ├── models.py            # ORM-моделі (6 сутностей)
│   ├── schemas.py           # Pydantic-схеми: валідація і відповіді
│   ├── errors.py            # доменні винятки, єдиний формат помилок
│   ├── routers/             # HTTP-шар
│   │   ├── common.py        #   спільні залежності та описи відповідей
│   │   ├── health.py
│   │   ├── users.py
│   │   ├── titles.py        #   каталог і тренди
│   │   └── tracking.py      #   списки, відмітки, прогрес
│   └── services/            # бізнес-логіка
│       ├── users.py
│       ├── titles.py
│       ├── tracking.py
│       └── stats.py
├── alembic/                 # міграції схеми БД
│   └── versions/0001_initial_schema.py
├── scripts/seed.py          # демо-дані
├── docker/entrypoint.sh     # міграції → сід → старт API
├── Dockerfile
├── docker-compose.yml
├── .env.example
├── requirements.txt / requirements-dev.txt
└── pyproject.toml           # конфігурація ruff (стиль коду)
```

---

## 8. Розподіл відповідальності (RACI)

Проєкт виконується одноосібно: R — Responsible (виконує), A — Accountable (відповідає за результат).

| Модуль / артефакт | Файли | Фрацовір Назар |
|---|---|----------------|
| Доменний аналіз, модель даних | `app/models.py`, ER-діаграма | R, A           |
| Міграції БД | `alembic/` | R, A           |
| Специфікація та реалізація API | `app/routers/`, `app/schemas.py` | R, A           |
| Бізнес-логіка трекінгу | `app/services/tracking.py` | R, A           |
| Каталог і аналітика | `app/services/titles.py`, `app/services/stats.py` | R, A           |
| Обробка помилок і валідація | `app/errors.py`, `app/schemas.py` | R, A           |
| Контейнеризація | `Dockerfile`, `docker-compose.yml`, `docker/` | R, A           |
| Архітектурні схеми | розділ 5 README | R, A           |
| Bottleneck Analysis | розділ 6 README | R, A           |
