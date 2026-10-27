"""Офлайн-проверка логики публикации: флаг --publish и защита от повторов.

Сеть не используется: отправка в Telegram подменена заглушкой, файлы
сообщений и сводки — временными. Ничего не публикуется и не читается
из data/messages.json.

Запуск:
    uv run python test_publish_flow.py
"""

import json
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import main as app  # noqa: E402
from telegramdigest.config import get_target_chat_id  # noqa: E402
from telegramdigest.console import setup_console  # noqa: E402
from telegramdigest.published import is_published, mark_published  # noqa: E402
from telegramdigest.sender import SendError  # noqa: E402

FAILURES = []

SAMPLE_TEXT = "Домовой дайджест\n\nПроблемы дома\n• тестовая строка"


def check(name, condition, detail=""):
    if condition:
        print(f"OK   {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


def _state_snapshot(path: Path) -> tuple:
    """Слепок файла состояния: есть ли он, что внутри, когда менялся."""
    if not path.exists():
        return (False, None, "")

    return (
        True,
        path.stat().st_mtime,
        path.read_text(encoding="utf-8"),
    )


def main() -> int:
    setup_console()

    import telegramdigest.published as published

    # Весь тест работает на временном файле состояния, чтобы не трогать
    # настоящий state/published.json.
    tmp_dir = tempfile.TemporaryDirectory()
    state_file = Path(tmp_dir.name) / "published.json"
    original_state_file = published.STATE_FILE
    published.STATE_FILE = state_file
    app.is_published = published.is_published
    app.mark_published = published.mark_published

    # Слепок настоящего файла состояния — после тестов он должен быть цел.
    real_state_before = _state_snapshot(original_state_file)

    try:
        # --- защита от повторов: цикл записи/чтения состояния ---
        check("до отправки текст не считается опубликованным", is_published(SAMPLE_TEXT) is False)

        mark_published(SAMPLE_TEXT, "-1001")
        check("после отметки текст считается опубликованным", is_published(SAMPLE_TEXT) is True)
        check("другой текст не считается опубликованным", is_published("другое") is False)

        saved = json.loads(state_file.read_text(encoding="utf-8"))
        check(
            "в состоянии есть хеш, чат и время",
            set(saved["published"][0]) == {"hash", "chat_id", "published_at"},
            str(saved["published"][0].keys()),
        )
        check(
            "в состоянии не лежит сам текст",
            SAMPLE_TEXT not in state_file.read_text(encoding="utf-8"),
        )

        # Много записей — файл обрезается.
        for index in range(60):
            mark_published(f"текст {index}", "-1001")
        saved = json.loads(state_file.read_text(encoding="utf-8"))
        check("хранится не больше 50 последних записей", len(saved["published"]) == 50)

        # Временный файл не остаётся рядом с состоянием.
        check(
            "временный файл записи удалён",
            not state_file.with_name(state_file.name + ".tmp").exists(),
        )

        run_publish_checks()

        # Настоящий state/published.json тест не тронул.
        check(
            "рабочий state/ не изменён",
            real_state_before == _state_snapshot(original_state_file),
            f"{original_state_file}",
        )
    finally:
        published.STATE_FILE = original_state_file
        app.is_published = is_published
        app.mark_published = mark_published
        tmp_dir.cleanup()

    print("-" * 50)
    if FAILURES:
        print(f"ПРОВАЛЕНО проверок: {len(FAILURES)}")
        return 1

    print("Все проверки пройдены.")
    return 0


def run_publish_checks() -> None:
    """Проверяет, что отправка происходит только с флагом --publish."""

    # --- публикация: отправка вызывается только с флагом --publish ---
    sent = []
    current_text = {"value": SAMPLE_TEXT}
    send_error = {"value": None}

    def fake_send(_token, chat_id, text):
        if send_error["value"] is not None:
            raise send_error["value"]

        sent.append((chat_id, text))
        return {"ok": True, "result": {"message_id": 42}}

    app.send_message = fake_send

    original_argv = sys.argv
    original_summary = app.generate_summary
    original_render = app.render_summary
    original_messages = app.MESSAGES_FILE
    original_preview = app.PREVIEW_FILE
    original_send = app.send_message

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        messages_file = tmp_path / "messages.json"
        preview_file = tmp_path / "summary_preview.txt"

        messages_file.write_text(
            json.dumps(
                {
                    "total_messages": 1,
                    "groups": [
                        {
                            "group_id": -100,
                            "group_title": "Дом",
                            "messages": [{"date": "2026-10-19T10:00:00", "text": "нет воды"}],
                        }
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        app.MESSAGES_FILE = messages_file
        app.PREVIEW_FILE = preview_file
        app.generate_summary = lambda _data, _key, _model: {
            "sections": [{"title": "Проблемы дома", "items": ["нет воды"]}],
            "messages": 1,
            "chunks": 1,
        }
        app.render_summary = lambda _summary, _data=None: current_text["value"]

        try:
            # Без --publish отправки быть не должно.
            sent.clear()
            sys.argv = ["main.py", "--skip-collect"]
            code = app.main()
            check("без --publish отправка не выполняется", sent == [] and code == 0)
            check("preview-файл создан", preview_file.exists())
            check("preview содержит текст сводки", SAMPLE_TEXT in preview_file.read_text(encoding="utf-8"))

            # С --publish отправка должна произойти.
            sent.clear()
            sys.argv = ["main.py", "--skip-collect", "--publish"]
            code = app.main()
            check("с --publish отправка выполняется", len(sent) == 1 and code == 0)
            check("в канал уходит отрендеренная сводка", sent and sent[0][1] == SAMPLE_TEXT)
            check(
                "целевой канал взят из конфигурации",
                sent and sent[0][0] == get_target_chat_id(),
                f"{sent[0][0] if sent else '—'} != {get_target_chat_id()}",
            )

            # Повторный запуск того же текста — публикации быть не должно.
            sent.clear()
            code = app.main()
            check(
                "повторная публикация того же текста пропущена",
                sent == [] and code == 0,
                f"отправок: {len(sent)}",
            )

            # Неудачная отправка не должна считаться успешной.
            failed_text = "Домовой дайджест\n\nПроблемы дома\n• этот текст не отправится"
            current_text["value"] = failed_text
            send_error["value"] = SendError("Telegram вернул HTTP 500. Ошибка на стороне Telegram.")

            sent.clear()
            sys.argv = ["main.py", "--skip-collect", "--publish"]
            code = app.main()
            check("при ошибке отправки код возврата 1", code == 1, f"код {code}")
            check("при ошибке отправки в канал ничего не ушло", sent == [])
            check(
                "неудачная отправка не отмечена как опубликованная",
                is_published(failed_text) is False,
            )

            send_error["value"] = None
            current_text["value"] = SAMPLE_TEXT
        finally:
            sys.argv = original_argv
            app.generate_summary = original_summary
            app.render_summary = original_render
            app.MESSAGES_FILE = original_messages
            app.PREVIEW_FILE = original_preview
            app.send_message = original_send


if __name__ == "__main__":
    raise SystemExit(main())
