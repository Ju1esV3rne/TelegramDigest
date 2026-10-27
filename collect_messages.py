"""Сбор сообщений из домовых Telegram-чатов за указанный период.

Запуск:
    uv run python collect_messages.py

Настройки (список чатов, период, путь к файлу) берутся из config.toml.
Секреты (API ID / API Hash) — из .env.
Результат сохраняется в JSON с русским текстом — файл готов для передачи в LLM.
"""

import asyncio
import json
import os
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
import os
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, RPCError

PROJECT_ROOT = Path(__file__).resolve().parent
ENV_FILE = PROJECT_ROOT / ".env"
CONFIG_FILE = PROJECT_ROOT / "config.toml"
SESSION_FILE = PROJECT_ROOT / "telegram_qr_session"


def setup_console() -> None:
    """Переключаем консоль на UTF-8, иначе русский текст в Windows ломается."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


async def safe_disconnect(client: TelegramClient) -> None:
    """Закрывает соединение.

    Telethon возвращает из disconnect() awaitable, только если цикл событий
    уже запущен, иначе — None. Поэтому проверяем результат.
    """
    result = client.disconnect()
    if result is not None:
        await result


def load_api_credentials() -> tuple[int, str]:
    """Читает API ID и API Hash из .env."""
    if ENV_FILE.exists():
        load_dotenv(ENV_FILE)

    api_id = os.getenv("TELEGRAM_API_ID", "").strip()
    api_hash = os.getenv("TELEGRAM_API_HASH", "").strip()

    missing = []
    if not api_id:
        missing.append("TELEGRAM_API_ID")
    if not api_hash:
        missing.append("TELEGRAM_API_HASH")
    if missing:
        raise SystemExit(
            f"Не заданы переменные: {', '.join(missing)}.\n"
            "Впишите их значения и запустите скрипт заново."
        )

    try:
        return int(api_id), api_hash
    except ValueError:
        raise SystemExit(
            "TELEGRAM_API_ID должен быть целым числом. "
            "Скопируйте его из my.telegram.org -> API development tools."
        ) from None


def load_config() -> dict:
    """Читает настройки из config.toml."""
    if not CONFIG_FILE.exists():
        raise SystemExit(
            f"Файл {CONFIG_FILE} не найден.\n"
            "Создайте его со списком чатов и запустите скрипт заново."
        )

    with CONFIG_FILE.open("rb") as file:
        config = tomllib.load(file)

    if not config.get("groups"):
        raise SystemExit(
            f"В файле {CONFIG_FILE} пустой список groups.\n"
            "Укажите хотя бы один ID чата, например: groups = [-1001122753791]"
        )

    return config


def get_message_text(message) -> str | None:
    """Возвращает текст сообщения или None, если текста нет.

    У картинок и видео текст может лежать в подписи (caption) — она тоже полезна.
    """
    if message.message:
        return message.message
    if message.media is not None and getattr(message.media, "caption", None):
        return message.media.caption
    return None


async def collect_group(
    client: TelegramClient, group_id: int, cutoff: datetime
) -> tuple[str, list[dict]]:
    """Собирает сообщения одного чата за период начиная с cutoff.

    Возвращает (название чата, список сообщений).
    """
    # get_input_entity возвращает "указатель" на чат, который понимает iter_messages.
    # get_entity — полный объект, из него берём название.
    peer = await client.get_input_entity(group_id)
    entity = await client.get_entity(group_id)
    title = getattr(entity, "title", None) or str(group_id)

    messages = []
    # reverse=True + offset_date=cutoff — это запрос "все сообщения НОВЕЕ cutoff".
    # Без reverse=True Telethon отдал бы наоборот — сообщения СТАРШЕ границы.
    # Так весь архив чата в память не подтягивается, а сообщения идут по возрастанию.
    async for message in client.iter_messages(peer, offset_date=cutoff, reverse=True):
        # Двойная страховка: если сообщение всё же старше границы — останавливаемся.
        if message.date and message.date < cutoff:
            break

        text = get_message_text(message)
        if not text:
            # Сообщение без текста (картинка, голосовое, стикер) — пропускаем.
            continue

        messages.append(
            {
                "message_id": message.id,
                "date": message.date.isoformat(),
                "text": text,
            }
        )

    return title, messages


async def main() -> int:
    setup_console()

    config = load_config()
    api_id, api_hash = load_api_credentials()

    groups = config["groups"]
    hours = int(config.get("hours", 24))
    output_path = PROJECT_ROOT / config.get("output", "data/messages.json")

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=hours)

    print(f"Чатов в настройках: {len(groups)}")
    print(f"Период: последние {hours} ч. (с {cutoff.strftime('%d.%m.%Y %H:%M')})")
    print(f"Подключаюсь к Telegram...")

    # Используем StringSession, если доступна; иначе, используем файл сессии
    session_str = os.getenv("TELEGRAM_SESSION")
    if session_str:
        client = TelegramClient(StringSession(session_str), api_id, api_hash)
    else:
        client = TelegramClient(str(SESSION_FILE), api_id, api_hash)

    try:
        await client.connect()
    except (FloodWaitError, OSError) as error:
        print(f"Не удалось подключиться к Telegram: {error}", file=sys.stderr)
        return 1

    try:
        if not await client.is_user_authorized():
            print(
                "Сессия не авторизована.\n"
                "Пройдите QR-авторизацию: uv run python test_qr.py",
                file=sys.stderr,
            )
            return 1

        result = {
            "generated_at": now.isoformat(),
            "period_hours": hours,
            "period_start": cutoff.isoformat(),
            "groups": [],
            "total_messages": 0,
            "errors": [],
        }

        for group_id in groups:
            print(f"\nОбрабатываю чат {group_id}...")
            try:
                title, messages = await collect_group(client, group_id, cutoff)
            except FloodWaitError as error:
                print(f"  Telegram попросил подождать {error.seconds} сек.")
                result["errors"].append(
                    {"group_id": group_id, "error": f"FloodWait: {error.seconds} сек."}
                )
                continue
            except RPCError as error:
                print(f"  Ошибка Telegram: {error}")
                result["errors"].append({"group_id": group_id, "error": str(error)})
                continue
            except Exception as error:  # noqa: BLE001 — один чат не должен ронять весь сбор
                print(f"  Не удалось прочитать чат: {type(error).__name__}: {error}")
                result["errors"].append(
                    {"group_id": group_id, "error": f"{type(error).__name__}: {error}"}
                )
                continue

            result["groups"].append(
                {
                    "group_id": group_id,
                    "group_title": title,
                    "message_count": len(messages),
                    "messages": messages,
                }
            )
            result["total_messages"] += len(messages)
            print(f"  {title}: найдено {len(messages)} сообщений")

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as file:
            json.dump(result, file, ensure_ascii=False, indent=2)

        print("\n" + "=" * 60)
        print(f"Групп обработано: {len(result['groups'])} из {len(groups)}")
        print(f"Сообщений собрано: {result['total_messages']}")
        print(f"Ошибок: {len(result['errors'])}")
        print(f"Результат сохранён: {output_path}")

        return 0 if not result["errors"] else 2

    finally:
        await safe_disconnect(client)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
