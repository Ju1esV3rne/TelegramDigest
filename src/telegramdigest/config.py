"""Загрузка конфигурации проекта из .env."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Корень проекта: src/telegramdigest/config.py -> ../../..
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
SESSION_DIR = PROJECT_ROOT / "sessions"

load_dotenv(ENV_FILE)


class ConfigError(RuntimeError):
    """В конфигурации не хватает обязательного значения."""


def _get(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(
            f"Не задана переменная {name}.\n"
            f"Откройте файл {ENV_FILE} и заполните его значениями."
        )
    return value


def get_telegram_api_id() -> int:
    raw = os.getenv("TELEGRAM_API_ID", "").strip()
    if not raw:
        raise ConfigError(
            "Не задана переменная TELEGRAM_API_ID.\n"
            f"Откройте файл {ENV_FILE} и впишите значение.\n"
            "Где взять: https://my.telegram.org -> API development tools -> "
            "App api_id (целое число)."
        )
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(
            f"TELEGRAM_API_ID должен быть целым числом. "
            f"Скопируйте его из my.telegram.org -> API development tools -> App api_id."
        ) from None


def get_telegram_api_hash() -> str:
    raw = os.getenv("TELEGRAM_API_HASH", "").strip()
    if not raw:
        raise ConfigError(
            "Не задана переменная TELEGRAM_API_HASH.\n"
            f"Откройте файл {ENV_FILE} и впишите значение.\n"
            "Где взять: https://my.telegram.org -> API development tools -> "
            "App api_hash (32 символа)."
        )
    return raw


def get_telegram_phone() -> str:
    """Номер телефона может быть пустым — тогда Telethon спросит при входе."""
    return os.getenv("TELEGRAM_PHONE", "").strip()


def get_openrouter_api_key() -> str:
    return _get("OPENROUTER_API_KEY")


def get_openrouter_model() -> str:
    return os.getenv("OPENROUTER_MODEL", "").strip() or "openai/gpt-4o-mini"


def get_bot_token() -> str:
    return _get("TELEGRAM_BOT_TOKEN")


def get_target_chat_id() -> str:
    return _get("TELEGRAM_TARGET_CHAT_ID")
