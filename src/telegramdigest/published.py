"""Защита от повторной публикации одного и того же дайджеста.

Хеш успешно отправленного текста сохраняется в state/published.json.
Состояние обновляется ТОЛЬКО после того, как Telegram подтвердил отправку:
при ошибке генерации или сбое отправки дайджест остаётся «неопубликованным»
и в следующий раз попадёт в канал снова.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_DIR = PROJECT_ROOT / "state"
STATE_FILE = STATE_DIR / "published.json"


def text_hash(text: str) -> str:
    """SHA-256 хеш текста дайджеста."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _load() -> dict:
    """Читает файл состояния. Отсутствие или битый файл — пустое состояние."""
    if not STATE_FILE.exists():
        return {"published": []}

    try:
        with STATE_FILE.open(encoding="utf-8") as file:
            data = json.load(file)
    except (json.JSONDecodeError, OSError):
        return {"published": []}

    if not isinstance(data, dict) or not isinstance(data.get("published"), list):
        return {"published": []}

    return data


def _save(data: dict) -> None:
    """Записывает состояние в файл.

    Запись атомарная: сначала пишем во временный файл рядом, затем
    переименовываем его. Если процесс прервётся посередине, старое состояние
    останется целым — иначе битый JSON означал бы потерю всей истории
    публикаций и возможность отправить дайджест повторно.
    """
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    temp_file = STATE_FILE.with_name(STATE_FILE.name + ".tmp")

    with temp_file.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    os.replace(temp_file, STATE_FILE)


def is_published(text: str) -> bool:
    """Публиковался ли уже такой текст дайджеста."""
    digest = text_hash(text)
    return any(item.get("hash") == digest for item in _load()["published"])


def mark_published(text: str, chat_id: str) -> None:
    """Запоминает дайджест как опубликованный.

    Вызывается только после успешного ответа Telegram.
    """
    data = _load()
    data["published"].append(
        {
            "hash": text_hash(text),
            "chat_id": chat_id,
            "published_at": datetime.now(timezone.utc).isoformat(),
        }
    )

    # Храним последние 50 записей — этого хватает для защиты от повторов
    # и не даёт файлу разрастаться бесконечно.
    data["published"] = data["published"][-50:]

    _save(data)
