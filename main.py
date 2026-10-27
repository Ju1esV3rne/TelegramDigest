"""Полный цикл: сбор сообщений → саммари через OpenRouter → публикация в Telegram.

Запуск (только генерация, без публикации — безопасный режим по умолчанию):
    uv run python main.py

Запуск с публикацией в канал:
    uv run python main.py --publish

Публикация выполняется только с явным флагом --publish.
Повторная публикация того же дайджеста блокируется: хеш успешно отправленного
текста хранится в state/published.json.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent

# Импортируем пакет проекта: src/telegramdigest лежит рядом.
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from telegramdigest.config import (  # noqa: E402
    ConfigError,
    get_bot_token,
    get_openrouter_api_key,
    get_openrouter_model,
    get_target_chat_id,
)
from telegramdigest.console import setup_console  # noqa: E402
from openai import APIStatusError  # noqa: E402
from telegramdigest.published import is_published, mark_published  # noqa: E402
from telegramdigest.sender import SendError, send_message  # noqa: E402
from telegramdigest.summary import (  # noqa: E402
    SummaryError,
    generate_summary,
    render_summary,
)

# Куда сохраняется готовый текст сводки для просмотра.
PREVIEW_FILE = PROJECT_ROOT / "data" / "summary_preview.txt"

# Файл с собранными сообщениями (создаётся collect_messages.py).
MESSAGES_FILE = PROJECT_ROOT / "data" / "messages.json"


def fail(*lines: str) -> None:
    """Печатает ошибку в stderr, сначала сбрасывая буфер stdout."""
    sys.stdout.flush()
    for line in lines:
        print(line, file=sys.stderr)
    sys.stderr.flush()


def run_collector() -> bool:
    """Запускает существующий сборщик сообщений.

    Сборщик вызывается отдельным процессом, чтобы его код не дублировался
    и оставался независимым от этого скрипта.
    """
    print("Запускаю сборщик сообщений...")
    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "collect_messages.py")],
        cwd=PROJECT_ROOT,
    )

    if result.returncode != 0:
        # Сборщик вернул 2, если часть чатов не удалась, — это не повод
        # останавливать цикл: данные всё равно записаны в JSON.
        if result.returncode == 2:
            print("Сборщик предупредил об ошибках в отдельных чатах.", file=sys.stderr)
        else:
            fail("Сборщик сообщений завершился с ошибкой.")
            return False

    return True


def load_messages() -> dict:
    """Читает собранные сообщения из JSON."""
    if not MESSAGES_FILE.exists():
        raise SummaryError(
            f"Файл {MESSAGES_FILE} не найден. Сначала запустите collect_messages.py."
        )

    with MESSAGES_FILE.open(encoding="utf-8") as file:
        return json.load(file)


def main() -> int:
    setup_console()

    parser = argparse.ArgumentParser(
        description="Сбор сообщений, генерация сводки через OpenRouter и (опционально) публикация."
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Опубликовать сводку в Telegram-канал. Без этого флага сводка только показывается.",
    )
    parser.add_argument(
        "--skip-collect",
        action="store_true",
        help="Не запускать сборщик, взять сообщения из существующего data/messages.json.",
    )
    args = parser.parse_args()

    # --- Шаг 0: проверяем доступы до того, как потратим время на сбор и модель ---
    bot_token = ""
    chat_id = ""

    if args.publish:
        try:
            bot_token = get_bot_token()
            chat_id = get_target_chat_id()
        except ConfigError as error:
            fail(f"ОШИБКА: {error}")
            return 1

    # --- Шаг 1: сбор сообщений ---
    if args.skip_collect:
        print("Сбор пропущен, беру готовий data/messages.json")
    elif not run_collector():
        return 1

    # --- Шаг 2: генерация сводки ---
    try:
        data = load_messages()
    except (SummaryError, json.JSONDecodeError, OSError) as error:
        fail(f"ОШИБКА: не удалось прочитать собранные сообщения — {error}")
        return 1

    if data.get("total_messages", 0) == 0:
        fail(
            "ОШИБКА: нет ни одного сообщения за указанный период.\n"
            "Публиковать нечего — сначала проверьте период в config.toml."
        )
        return 1

    try:
        api_key = get_openrouter_api_key()
        model = get_openrouter_model()
    except ConfigError as error:
        fail(f"ОШИБКА: {error}")
        return 1

    print(f"Сообщений: {data['total_messages']} | модель: {model}")

    try:
        summary = generate_summary(data, api_key, model)
    except SummaryError as error:
        fail(f"ОШИБКА генерации сводки: {error}")
        return 1
    except APIStatusError as error:
        # Тело ответа OpenRouter может быть большим и содержать служебные
        # поля — показываем только код и короткое описание.
        status = getattr(error, "status_code", "?")
        detail = str(getattr(error, "message", "") or "")[:200]
        hint = ""
        if status == 429:
            hint = (
                "\nПохоже, исчерпан дневной лимит бесплатных запросов к OpenRouter. "
                "Он обновится завтра, либо можно добавить кредиты на openrouter.ai "
                "и указать платную модель в OPENROUTER_MODEL."
            )
        fail(
            f"ОШИБКА при обращении к OpenRouter: HTTP {status}. {detail}{hint}\n"
            "Публикация не выполняется, чтобы в канал не попало пустое или выдуманное сообщение."
        )
        return 1
    except Exception as error:  # noqa: BLE001 — сетевые и API-ошибки openai
        detail = str(error)[:200]
        fail(
            f"ОШИБКА при обращении к OpenRouter: {type(error).__name__}. {detail}\n"
            "Публикация не выполняется, чтобы в канал не попало пустое или выдуманное сообщение."
        )
        return 1

    text = render_summary(summary, data)

    # Сохраняем текст для просмотра.
    PREVIEW_FILE.parent.mkdir(parents=True, exist_ok=True)
    PREVIEW_FILE.write_text(text, encoding="utf-8")

    print(f"Разделов в сводке: {len(summary['sections'])}")
    print(f"Текст сохранён: {PREVIEW_FILE}")
    print("=" * 60)
    print(text)
    print("=" * 60)

    # --- Шаг 3: публикация (только по явному флагу) ---
    if not args.publish:
        print("\nРежим просмотра. Для публикации в канал добавьте флаг --publish")
        return 0

    # Токен и ID канала уже проверены в шаге 0 — здесь только используем их.
    if is_published(text):
        print("\nПубликация пропущена: такой дайджест уже отправлялся в канал.")
        return 0

    print(f"\nПубликую в канал {chat_id}...")
    try:
        result = send_message(bot_token, chat_id, text)
    except SendError as error:
        fail(
            f"ОШИБКА отправки: {error}\n"
            "Дайджест не отмечен как опубликованный — его можно отправить повторно."
        )
        return 1

    # Помечаем опубликованным только после успешного ответа Telegram.
    try:
        mark_published(text, chat_id)
    except OSError as error:
        fail(
            "ВНИМАНИЕ: сообщение отправлено, но состояние публикации не сохранено — "
            f"{error}\n"
            "При следующем запуске этот дайджест может опубликоваться повторно."
        )
        return 1

    message_id = result.get("result", {}).get("message_id", "—")
    print(f"УСПЕХ: опубликовано, ID сообщения {message_id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
