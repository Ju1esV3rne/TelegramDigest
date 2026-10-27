# PROJECT_AUDIT.md (TelegramDigest) — read-only аудит

> Обновлять по мере работы над проектом. Данный файл является доп. справочником к `AGENTS.md`. Ссылки в `AGENTS.md` ведут сюда для понимания архитектуры, рисков и структуры.

## 1) Data flow

### Шаг 1. Сбор сообщений
- **Точка входа:** `main.py:57-78` (`run_collector()`) при отсутствии `--skip-collect` — `subprocess.run([sys.executable, "collect_messages.py"], cwd=PROJECT_ROOT)`.
- **Исполнитель:** `collect_messages.py:156-248` (`async def main()`)
  1. `collect_messages.py:29-39` `setup_console()` — UTF-8 (Windows)
  2. `collect_messages.py:86-103` `load_config()` — `config.toml` (`groups`, `hours`, `output`). `SystemExit` при пустом/отсутствующем.
  3. `collect_messages.py:52-83` `load_api_credentials()` — `.env` через `python-dotenv`, требует `TELEGRAM_API_ID`/`TELEGRAM_API_HASH`, `api_id` -> `int`. `SystemExit` при ошибках.
  4. `collect_messages.py:166-174` — `cutoff = now - timedelta(hours=hours)`, `TelegramClient(str(SESSION_FILE), api_id, api_hash)`. `SESSION_FILE = PROJECT_ROOT / "telegram_qr_session"`.
  5. `collect_messages.py:175-189` — `connect()`, `is_user_authorized()`. Если нет — подсказка `test_qr.py`, возврат `1`.
  6. `collect_messages.py:199-240` — по группам `collect_group()` (`118-153`): `get_input_entity()`+`get_entity()`, `iter_messages(peer, offset_date=cutoff, reverse=True)` с проверкой `message.date < cutoff`. `get_message_text()` (`106-115`): `message.message` или `message.media.caption`; пустые пропускаются. Сбор `{"message_id","date"(isoformat),"text"}`.
  7. `collect_messages.py:231-241` — mkdir, `json.dump` с `ensure_ascii=False, indent=2` в `output` из `config.toml` (по умолчанию `data/messages.json`). Возврат `0`, либо `2` при ошибках по отдельным чатам.

**Связь:** `main.py:69-78` — код `2` трактуется как предупреждение (продолжает), иначе ошибка → `1`.

### Шаг 2. Загрузка и проверка
- `main.py:81-90` `load_messages()` — читает `MESSAGES_FILE` (по умолчанию `PROJECT_ROOT/data/messages.json`). При отсутствии — `SummaryError`.
- `main.py:129-140` — проверка `data.get("total_messages",0)==0` → ошибка, публикация не выполняется.
- `main.py:142-148` — `get_openrouter_api_key()`/`get_openrouter_model()` из `.env`/конфига.

### Шаг 3. Генерация сводки через OpenRouter
- `main.py:152-179` → `summary.generate_summary(data, api_key, model)`
- `summary.py:367-386` `generate_summary()`: `collect_rows()` (`103-126`, обрезка до `MAX_MESSAGE_CHARS=1200`, `…`), `split_into_chunks()` (`129-152`, `MAX_CHUNK_CHARS=12000`, строки `[{date}] {group}: {text}`), для каждого чанка `build_prompt()` + `request_sections()`, `merge_sections()` (`278-293`, мерж по нормализованному заголовку, дедупликация пунктов).
- `request_sections()` (`296-364`): `OpenAI(api_key, base_url=OPENROUTER_BASE_URL, timeout=REQUEST_TIMEOUT, max_retries=2)` (`21,305-310`, `OPENROUTER_BASE_URL="https://openrouter.ai/api/v1"`, `REQUEST_TIMEOUT=120.0`). Цикл до `MAX_ATTEMPTS=3` (`38`), при `problem` добавляет подсказку (`317-323`). Вызов `chat.completions.create(model, messages=[{"role":"system","content":SYSTEM_PROMPT},{"role":"user","content":user_prompt}], max_tokens=MAX_TOKENS, temperature=0.3)` (`324-332`, `MAX_TOKENS=8000`). Пустой `content` → повторы (`337-346`). `parse_sections()` (`245-275`) + `_extract_json()` (`216-242`, поддержка ```json```), фоллбэк в `"Сводка"`. `normalize_title()` (`162-190`), `validate_sections()` (`193-213`, белый список заголовков + `PLACEHOLDER_PATTERN`). Повтор при ошибке. При исчерпании попыток — `SummaryError`.

### Шаг 4. Рендер превью
- `main.py:181-191` — `text = render_summary(summary, data)`, mkdir `data/`, `PREVIEW_FILE.write_text(text, encoding="utf-8")`, вывод в консоль.
- `summary.py:389-406` `render_summary()`: `Домовой дайджест`, при наличии `period_start`/`period_hours` — строка периода, разделы с буллетами `• ...`.

### Шаг 5. Публикация (только `--publish`)
- `main.py:194-197` — без флага → выход `0`.
- `main.py:114-121` — при `--publish` проверка `get_bot_token()`/`get_target_chat_id()`, ошибка → `1`.
- `main.py:199-201` — `is_published(text)` по хешу из `state/published.json`. Если есть → пропуск.
- `main.py:203-212` — `send_message(bot_token, chat_id, text)` (`src/telegramdigest/sender.py`). `SendError` → `1`, **не** помечается.
- `main.py:214-226` — `mark_published(text, chat_id)` (`src/telegramdigest/published.py`) **только после успешного ответа**. Ошибка сохранения → предупреждение, `1` (сообщение уже отправлено).
- `sender.py:50-107` — POST `https://api.telegram.org/bot{token}/sendMessage` JSON `{"chat_id","text"}`, таймаут 30с, urllib. Токен в URL, не логируется. Проверка `status==200`, `result.get("ok")`.
- `published.py:9-83` — SHA-256 (`text_hash`), `_load()` из `STATE_FILE = PROJECT_ROOT/state/published.json` (пустой при отсутствии/битом), `_save()` атомарно `.tmp` + `os.replace`. `mark_published` добавляет `{"hash","chat_id","published_at"}`, обрезает до **последних 50**.

## 2) Карта файлов

### Core application files
| Файл | Назначение | Комментарий |
|---|---|---|
| `main.py` | Оркестрация цикла, `--publish`/`--skip-collect`, координация сбора/генерации/публикации. | Фактический entry-point цикла. |
| `collect_messages.py` | Сбор из Telegram через Telethon от пользовательского аккаунта, сохранение в JSON. | Вызывается subprocess'ом из `main.py`. Работает с `telegram_qr_session`. |
| `src/telegramdigest/config.py` | Загрузка `.env`, валидация/получение секретов. | `PROJECT_ROOT = parents[2]`, `SESSION_DIR = PROJECT_ROOT / "sessions"` (есть, не используется в активных вызовах). |
| `src/telegramdigest/summary.py` | OpenRouter (openai-клиент), промпты, нарезка, валидация, нормализация, мерж, рендер. | Ключевая логика ИИ. |
| `src/telegramdigest/sender.py` | Отправка через Bot API, маппинг HTTP-ошибок. | urllib. |
| `src/telegramdigest/published.py` | Защита от дублей, хранение хешей. | Атомарная запись, лимит 50. |
| `src/telegramdigest/tg_client.py` | Обёртка Telethon для авторизации/подключения. | `SESSION_NAME="telegram_qr_session"`, `get_session_path()` → корень проекта. Используется `list_dialogs.py`. |
| `src/telegramdigest/console.py` | Настройка консоли (UTF-8). | Вспомогательный. |
| `src/telegramdigest/list_dialogs.py` | Утилита просмотра диалогов (использует `tg_client.create_client`). | Не участвует в основном цикле. |
| `src/telegramdigest/__init__.py` | Пакет. | Содержимое не анализировалось детально (гипотеза — минимальное влияние на цикл). |

### Configuration
| Файл | Назначение | Комментарий |
|---|---|---|
| `pyproject.toml` | Метаданные, зависимости, `[project.scripts] telegramdigest = "telegramdigest:main"`, `uv_build`. | `requires-python >=3.14`. |
| `config.toml` | `groups`, `hours`, `output`. | Без секретов. |
| `.env.example` | Шаблон переменных. | Справочный. |
| `.gitignore` | Исключает `.env`, `*.session*`, `sessions/`, `state/`, `data/`, `__pycache__`, `.venv`. | Соответствует политике. |
| `.python-version`, `uv.lock` | Конфигурация/lock. | Генерируемые/зафиксированные. |

### Tests and development utilities
| Файл | Назначение | Комментарий |
|---|---|---|
| `test_summary.py` | Офлайн-тесты сводки. | Без сети/данных чата. |
| `test_publish_flow.py` | Офлайн-тесты `--publish` и дублей. | Подмена зависимостей, временные файлы. |
| `test_sender.py` | Тесты отправки/ошибок. | Утилита. |
| `test_bot.py` | Тест бота. | Утилита. |
| `test_qr.py` | QR-авторизация Telethon (`telegram_qr_session`). | Утилита. |

### Generated data and runtime state
| Файл/директория | Назначение | Комментарий |
|---|---|---|
| `data/` | `messages.json`, `summary_preview.txt`. | Игнорируется git, может содержать персональные данные. |
| `state/` | `published.json`. | Игнорируется git. |
| `sessions/` | Для сессий. | `SESSION_DIR` есть, не используется в активных `TelegramClient`. |
| `telegram_qr_session.session` (корень), `telegram_session.session` (корень), `sessions/user.session` | Сессии Telethon. | См. ниже. |

### Potentially redundant or obsolete files
| Файл | Статус (гипотеза) | Пояснение |
|---|---|---|
| `telegram_session.session` (корень) | **Вероятно избыточный** | Нет явных ссылок в коде, активные клиенты используют `telegram_qr_session`. |
| `sessions/user.session` | **Вероятно избыточный** | Нет явных ссылок, `SESSION_DIR` не используется. |
| `src/telegramdigest/__init__.py` | **Неясно** | Требует проверки содержимого для точного вывода. |
| `src/telegramdigest/list_dialogs.py` | **Утилитарный** | Не участвует в основном цикле. |

## 3) Интеграция с OpenRouter

- **API-клиент:** `summary.py:21` `OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"`, `summary.py:305-310` `OpenAI(api_key, base_url, timeout=REQUEST_TIMEOUT, max_retries=2)`, `REQUEST_TIMEOUT=120.0`
- **Ключ:** `main.py:143` → `config.get_openrouter_api_key()` (`config.py:65-66`)
- **Модель:** `main.py:144` → `config.get_openrouter_model()` (`config.py:69-70`), дефолт `openai/gpt-4o-mini`, передаётся в `generate_summary` и `request_sections()`
- **Промпты:** `summary.py:57-96` `SYSTEM_PROMPT` (только факты, строгий JSON, фиксированный список заголовков, запрет заглушек). `summary.py:155-159` `build_prompt()`. При повторах — добавочная подсказка `summary.py:317-323`
- **Ответы/ошибки:** `_extract_json()` (`216-242`), `parse_sections()` (`245-275`) с фоллбэком `"Сводка"`, `normalize_title()` (`162-190`), `validate_sections()` (`193-213`), повторы до 3 раз (`314-360`), пустой ответ → повторы. В `main.py:156-179` обработка `APIStatusError` (спец-подсказка 429) и прочих исключений — при ошибке публикация не выполняется.

## 4) Потенциальные проблемы

### Дублирующая/несогласованная логика
- **Дублирование путей сессии:** `collect_messages.py:26` vs `tg_client.py:19,22-23`. Одно и то же имя — путаница (возможный риск).
- **Два способа подключения:** `collect_messages.py` самостоятельный, `tg_client.py` + `list_dialogs.py` — пересекаются (возможный риск).
- **Неиспользуемая `SESSION_DIR`:** `config.py:11` есть, не используется в активных `TelegramClient` (факт, возможный риск недопонимания).

### Хрупкие пути
- **Разные способы указания сессии:** `test_qr.py:17` относительный `"telegram_qr_session"`, остальные от `PROJECT_ROOT`. Зависит от cwd (возможный риск, гипотеза).
- **Зависимость на cwd:** `main.py:65` с `cwd=PROJECT_ROOT`, но скрипты чувствительны к cwd (частично учтено).
- **Пути через `parents[...]`:** работают при текущей структуре, ломаются при перемещении (потенциальный риск).

### Пробелы в обработке ошибок
- **main.py:69-78:** при ошибке сборщика !=0 и !=2 — мало деталей (возможный риск).
- **collect_messages.py:177-179:** скудный совет при ошибке connect() (небольшой пробел).
- **summary.py:173-179:** обрезка деталей до 200 символов (возможный риск).
- **main.py:214-226:** сбой `mark_published()` после успешной отправки — сообщение уже ушло, риск повтора при следующем запуске (факт, код предупреждает).

### Повторные запуски / дублирование публикации
- **Защита есть:** хеш, проверка перед отправкой, отметка только после успеха, атомарная запись (факт).
- **Риск повтора:** при сбое между отправкой и `mark_published()` `is_published()` вернёт false — может отправить повторно (факт риска, задокументирован).
- **Лимит 50 записей:** `published.py:79-81` — старые записи удаляются (возможный риск).
- **Хеш всего текста:** изменение `render_summary()` меняет хеш — защита может не сработать (возможный риск).

### Ограничения длины сообщения / Telegram API
- **Нет проверки длины перед отправкой:** `sender.py` шлёт как есть, README упоминает 4096, явной проверки/нарезки нет (факт отсутствия, риск).
- **Нарезка только для промпта:** не для итогового текста (факт).
- **Таймауты:** `sender.py:16` 30с, `summary.py:30` 120с — обычно ок, при медленной сети риск (возможный риск).

### Надёжность при регулярном использовании
- **Зависимость от сессии Telethon:** требует авторизованной `telegram_qr_session` (факт).
- **Ручной запуск:** нет планировщика (факт).
- **OpenRouter лимиты:** обрабатываются (429/пустой ответ/повторы), но при исчерпании — ошибка, публикация не случится (факт).
- **Частичные ошибки сбора:** при ошибке одного чата сбор продолжает, сводка может быть по неполному набору (факт).
- **`--skip-collect`:** берёт `data/messages.json` без проверки свежести (возможный риск).
- **Сессии:** активны по коду только `telegram_qr_session` (корень). `telegram_session.session` (корень), `sessions/user.session` не имеют ссылок. `SESSION_DIR` не используется (факт).

## 5) Текущее состояние

### Реализовано
- Цикл сбора → генерации → превью → опциональной публикации с `--publish`
- Сбор через Telethon от пользовательского аккаунта
- Генерация через OpenRouter с жёсткой валидацией и повторами
- Защита от дублей по хешу с атомарной записью
- Гварды против случайной публикации
- Офлайн-тесты ключевой логики

### Поддерживается кодом
- Конфиг через `config.toml`/`.env`
- Нарезка на чанки, нормализация заголовков, мерж, дедупликация
- Фоллбэк при неразбираемом JSON
- Обработка 429/пустого ответа
- Частичные ошибки при сборе по чатам

### Нельзя подтвердить без запуска
- Актуальность/валидность `telegram_qr_session` сессии
- Доступность OpenRouter/квоты
- Права бота в целевом канале
- Содержимое `data/messages.json`/`state/published.json`
- Работоспособность в конкретном окружении с VPN
- Принадлежность `sessions/user.session`/`telegram_session.session` аккаунту

# Списки для принятия решения

## 1) Оставить без изменений
- `src/telegramdigest/summary.py` — основная логика валидации/нормализации под OpenRouter (интеграция намеренная)
- `src/telegramdigest/published.py` — корректная защита от дублей, атомарная запись
- `src/telegramdigest/sender.py` — минималистичная отправка через Bot API
- `main.py` — оркестрация с гвардами (проверка токенов перед публикацией, отметка только после успеха)
- `collect_messages.py` — сбор с обработкой частичных ошибок
- Тесты `test_summary.py`, `test_publish_flow.py` — хорошее офлайн-покрытие

## 2) Потенциально упростить
- **Унифицировать пути сессии** — использовать `tg_client.get_session_path()` вместо `SESSION_FILE` в `collect_messages.py` и хардкода в `test_qr.py`
- **Убрать/использовать `SESSION_DIR`** в `src/telegramdigest/config.py`
- **Перевести `collect_messages.py` на `tg_client`** где возможно (гипотеза)
- **Очистить неиспользуемые сессии** (`telegram_session.session`, `sessions/user.session`) **только после** подтверждения рабочей `telegram_qr_session.session`

## 3) Проверить или доработать позже
- **Проверка длины сообщения перед отправкой** — добавить валидацию/нарезку (лимит Telegram ~4096)
- **Обработка сбоя `mark_published`** — продумать стратегию (сообщение уже отправлено, риск повтора)
- **Диагностика ошибок сборщика** — пробрасывать детали в `main.py`
- **Проверить `sessions/`** — использовать или убрать
- **Проверить `src/telegramdigest/__init__.py`** — содержимое
- **Лимит 50 записей `published.json`** — оценить при регулярных запусках
- **Унифицировать пути сессий** (особенно относительный путь в `test_qr.py`)

> Файл создан в рамках read-only аудита. Не вносит изменений в код/данные. Обновлять при изменениях архитектуры/рисков.