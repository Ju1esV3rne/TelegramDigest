# AGENTS.md (TelegramDigest)

## Описание
Локальный инструмент для сводки по домовым Telegram-чатам. 
Цикл: `collect_messages.py` (Telethon, аккаунт пользователя) → `data/messages.json` → `main.py` генерирует сводку через OpenRouter → `data/summary_preview.txt`. Публикация в канал бота — **только** с флагом `--publish` через Bot API, с защитой от дублей в `state/published.json` (отметка только после успешной отправки).

## Карта проекта
- `main.py` — основной цикл (вызывает `collect_messages.py` subprocess'ом). Скрипт `pyproject.toml` `telegramdigest = "telegramdigest:main"` есть, но для цикла используется корневой `main.py`.
- `collect_messages.py` — сбор сообщений за период.
- `src/telegramdigest/` — `config.py` (.env), `tg_client.py` (Telethon), `summary.py` (OpenRouter/openai, валидация/нормализация/мерж), `sender.py` (Bot API), `published.py` (хеши, атомарная запись), `console.py`, `list_dialogs.py`.
- Конфиг: `config.toml` (`groups`, `hours`, `output`). Данные/состояние: `data/`, `state/`. 
- **Сессии Telethon:** каноническая по коду — корневая `telegram_qr_session` (используют `collect_messages.py`, `src/telegramdigest/tg_client.py`, `test_qr.py`, `src/telegramdigest/list_dialogs.py`). Файлы `telegram_session.session` (корень) и `sessions/user.session` **не имеют ссылок в коде** — считаются неактивными. Не трогать.
- Тесты: `test_summary.py`, `test_publish_flow.py`, `test_sender.py`, `test_bot.py`, `test_qr.py` (предпочтительно офлайн).
- **Детальный аудит:** см. `PROJECT_AUDIT.md` (data flow, OpenRouter-интеграция, риски, карта файлов). Обновлять при изменениях проекта.

## Конфигурация
- Python >= 3.14, зависимости в `pyproject.toml`, сборка `uv_build`. Проект использует `uv`.
- `.env`: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` (+ опц. `TELEGRAM_PHONE`), `OPENROUTER_API_KEY`, опц. `OPENROUTER_MODEL` (по умолчанию `openai/gpt-4o-mini`), `TELEGRAM_BOT_TOKEN`, `TELEGRAM_TARGET_CHAT_ID`. `.env` в `.gitignore`.
- `config.toml` задаёт чаты и период сбора.
- **GitHub Actions:** Используется переменная окружения `TELEGRAM_SESSION` (строковый сеанс) — если она не пуста, клиент создаётся с `StringSession`, сохраняя файловую сессию (`telegram_qr_session`) локально для обычного использования.

## Запуск команд через shell
- **Долгие команды** (`uv run python main.py`, `collect_messages.py`, с подключением к Telegram/OpenRouter) — всегда запускать с `timeout >= 300000` (5 минут). При VPN/нестабильной сети лучше 600000 (10 минут).
- **Короткие команды** (`--version`, проверка файлов) — можно с меньшим таймаутом.
- **PowerShell** — не использовать `&&`, `grep`, `tail`, `head`. Фильтровать через `Select-Object -First/-Last`.
- **Без фонового режима** для `main.py` — нужен полный вывод.
- **Если таймаут** — не повторять с маленьким значением, сразу увеличивать до адекватного.

## Безопасные команды
- Установка: `uv sync`
- Генерация без публикации: `uv run python main.py`
- Сбор только: `uv run python collect_messages.py`
- Генерация по готовым данным: `uv run python main.py --skip-collect`
- Тесты (офлайн): `uv run python test_summary.py`, `uv run python test_publish_flow.py`, `uv run python test_sender.py`

**Важно:** `uv run python main.py --publish` требует **явного разрешения**. Без флага публикации нет.

## Правила
- Язык общения — русский.
- Проект простой: избегайте лишних абстракций, рефакторинга, зависимостей и файлов.
- Перед нетривиальными изменениями — понять проблему, предложить план, при неясностях спросить.
- Менять только необходимое. 
- **Защита данных/секретов:** никогда не показывать `.env`, не удалять/перезаписывать сессии Telethon, не трогать реальные файлы в `data/` и `state/`. Тесты изолировать от реальных данных.
- **Строгие запреты без явного разрешения:** публикация в Telegram, вызов live API, изменение учётных данных, установка зависимостей, деструктивные операции, коммиты/пуш.
- **Тесты:** смотреть тесты перед запуском, предпочитать офлайн и временные файлы; различать успех юнит-теста и реальную интеграционную проверку.
- **Честность:** сообщать реальные изменения, проведённые проверки и нерешённые вопросы. Не утверждать, что действие/проверка выполнены, если это не так.
