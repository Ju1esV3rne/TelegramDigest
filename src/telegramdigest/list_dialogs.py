"""Этап 2: подключение к Telegram и вывод списка доступных диалогов.

Запуск:
    uv run python -m telegramdigest.list_dialogs

Скрипт только читает список чатов. Историю сообщений не выгружает.
"""

import argparse
import asyncio
import sys

from telethon.errors import FloodWaitError, SessionPasswordNeededError

from .config import ConfigError
from .console import setup_console
from .tg_client import create_client, disconnect, print_flood_wait


async def main() -> int:
    setup_console()

    parser = argparse.ArgumentParser(
        description="Подключение к Telegram и вывод списка доступных диалогов."
    )
    parser.add_argument(
        "--code",
        help="Код подтверждения из Telegram (для неинтерактивного запуска).",
    )
    args = parser.parse_args()

    try:
        client = await create_client(code=args.code)
    except ConfigError as exc:
        print(f"Ошибка конфигурации:\n{exc}", file=sys.stderr)
        return 1
    except SessionPasswordNeededError:
        print(
            "Нужен пароль двухфакторной аутентификации (2FA). "
            "Введите его в консоли при запросе.",
            file=sys.stderr,
        )
        return 1
    except FloodWaitError as exc:
        print_flood_wait(exc)
        return 1
    except Exception as exc:  # noqa: BLE001 — понятное сообщение вместо трейсбека
        print(f"Не удалось подключиться к Telegram: {exc}", file=sys.stderr)
        return 1

    me = await client.get_me()
    print(f"Авторизован как: {me.first_name} {me.last_name or ''} (@{me.username})")
    print(f"ID аккаунта: {me.id}")
    print("-" * 60)

    try:
        dialogs = await client.get_dialogs(limit=100)
    except FloodWaitError as exc:
        print_flood_wait(exc)
        await disconnect(client)
        return 1
    except Exception as exc:  # noqa: BLE001
        print(f"Не удалось получить список диалогов: {exc}", file=sys.stderr)
        await disconnect(client)
        return 1

    if not dialogs:
        print("Диалогов не найдено.")

    for dialog in dialogs:
        entity = dialog.entity
        kind = "группа" if getattr(entity, "megagroup", False) else "чат"
        if getattr(entity, "broadcast", False):
            kind = "канал"
        title = getattr(entity, "title", None) or "—"
        print(f"{str(entity.id):>14} | {kind:6} | {title}")

    print("-" * 60)
    print(f"Всего диалогов: {len(dialogs)}")

    await disconnect(client)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
