"""Проверка отправки сообщения Telegram-ботом через Bot API.

ВНИМАНИЕ: скрипт отправляет НАСТОЯЩЕЕ сообщение в канал «Домовой дайджест».
Запускайте его вручную, чтобы убедиться, что бот работает. Автоматические
проверки публикации живут в test_publish_flow.py и в Telegram не пишут.

Запуск:
    uv run python test_bot.py

Скрипт НЕ использует пользовательскую Telethon-сессию — только Bot API.
Токен бота читается из .env и никогда не попадает в вывод: в логах
показывается лишь адрес api.telegram.org без токена.

Саму отправку делает общий модуль telegramdigest.sender — тот же код
использует main.py при публикации дайджеста.
"""

import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from telegramdigest.sender import SendError, send_message  # noqa: E402

ENV_FILE = PROJECT_ROOT / ".env"

# Канал-получатель теста.
TARGET_CHAT_ID = "-1004412888640"

# Текст тестового сообщения.
TEST_TEXT = "Тест отправки от TelegramDigest. Бот работает!"



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


def load_bot_token() -> str:
    """Читает токен бота из .env."""
    if not ENV_FILE.exists():
        raise SystemExit(
            f"Файл {ENV_FILE} не найден.\n"
            "Скопируйте .env.example в .env и впишите TELEGRAM_BOT_TOKEN."
        )

    load_dotenv(ENV_FILE)
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

    if not token:
        raise SystemExit(
            f"В файле {ENV_FILE} не задана переменная TELEGRAM_BOT_TOKEN.\n"
            "Получите токен у @BotFather и впишите его в .env."
        )

    return token


def fail(*lines: str) -> None:
    """Печатает ошибку в stderr, сначала сбрасывая буфер stdout.

    Нужно, чтобы сообщение об ошибке не «перепрыгнуло» основной текст:
    stdout буферизуется, stderr — нет.
    """
    sys.stdout.flush()
    for line in lines:
        print(line, file=sys.stderr)
    sys.stderr.flush()


def main() -> int:
    setup_console()

    token = load_bot_token()

    print(f"Отправляю сообщение в канал {TARGET_CHAT_ID}...")
    print(f"Текст: {TEST_TEXT}")
    print("-" * 60)
    sys.stdout.flush()

    try:
        result = send_message(token, TARGET_CHAT_ID, TEST_TEXT)
    except SendError as error:
        fail(
            f"ОШИБКА: {error}",
            "",
            "Что проверить:",
            "  - бот @OdinDigestBot добавлен в канал «Домовой дайджест»;",
            "  - у бота есть право публикации сообщений в этом канале;",
            "  - токен в .env действителен.",
        )
        return 1

    message = result.get("result", {})
    print(f"ok в ответе: {result.get('ok')}")
    print(f"ID отправленного сообщения: {message.get('message_id', '—')}")
    print(f"ID чата: {message.get('chat', {}).get('id', '—')}")
    print("-" * 60)
    print("УСПЕХ: сообщение отправлено.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
