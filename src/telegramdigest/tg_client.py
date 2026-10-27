"""Обёртка над Telethon: подключение, авторизация, безопасное завершение."""

import sys
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import FloodWaitError, SessionPasswordNeededError

from .config import (
    PROJECT_ROOT,
    get_telegram_api_hash,
    get_telegram_api_id,
    get_telegram_phone,
)

# Имя файла сессии. Содержит токен доступа к Telegram — поэтому файлы
# *.session добавлены в .gitignore и никогда не коммитятся.
# Используем ту же сессию, что и test_qr.py / collect_messages.py.
SESSION_NAME = "telegram_qr_session"


def get_session_path() -> Path:
    return PROJECT_ROOT / SESSION_NAME


async def create_client(code: str | None = None) -> TelegramClient:
    """Создаёт клиента и выполняет авторизацию при первом запуске.

    При первом запуске Telethon запросит код подтверждения из Telegram
    и, если включена двухфакторная аутентификация, пароль 2FA.

    Если передан `code`, код подтверждения берётся из него, а не из консоли.
    Это нужно для неинтерактивного запуска.
    """
    session_path = get_session_path()
    client = TelegramClient(
        str(session_path),
        get_telegram_api_id(),
        get_telegram_api_hash(),
    )

    phone = get_telegram_phone()
    if code:
        # Неинтерактивный режим: отправляем код и подтверждаем сразу.
        await client.connect()
        if not await client.is_user_authorized():
            sent = await client.send_code_request(phone)
            try:
                await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
            except SessionPasswordNeededError:
                password = input("Пароль 2FA: ")
                await client.sign_in(password=password)
    elif phone:
        await client.start(phone=phone)
    else:
        # Telethon сам спросит номер телефона и код в консоли.
        await client.start()

    return client


async def disconnect(client: TelegramClient) -> None:
    """Корректно закрывает соединение, не удаляя файл сессии."""
    try:
        await client.disconnect()
    except FloodWaitError:
        # Telegram просит подождать — на disconnect это не критично.
        pass


def print_flood_wait(error: FloodWaitError) -> None:
    """Понятное сообщение о лимите Telegram."""
    print(
        f"Telegram попросил подождать {error.seconds} сек. "
        "Повторите попытку позже.",
        file=sys.stderr,
    )
