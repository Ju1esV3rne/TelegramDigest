"""Офлайн-проверка отправки в Telegram: разбор ответов и обработка ошибок.

Сеть не используется: вызов urllib.request.urlopen подменяется заглушкой,
которая возвращает заданный ответ или бросает заданную ошибку.

Запуск:
    uv run python test_sender.py
"""

import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from telegramdigest.console import setup_console  # noqa: E402
from telegramdigest.sender import SendError, send_message  # noqa: E402

FAILURES = []

FAKE_TOKEN = "123456:FAKE-TOKEN-DO-NOT-USE"
FAKE_CHAT = "-1001234567890"


def check(name, condition, detail=""):
    if condition:
        print(f"OK   {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


class FakeResponse:
    """Заглушка ответа urlopen."""

    def __init__(self, status, body):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def expect_error(name, expected_parts, error):
    """Проверяет, что ошибка SendError содержит все ожидаемые фрагменты."""
    if not isinstance(error, SendError):
        check(name, False, f"получено {type(error).__name__}, а не SendError")
        return

    text = str(error)
    missing = [part for part in expected_parts if part.lower() not in text.lower()]

    check(name, not missing, f"нет фрагментов {missing} в «{text}»")

    # Токен не должен попадать ни в одно сообщение об ошибке.
    check(
        f"{name}: токен не утекает в текст ошибки",
        FAKE_TOKEN not in text and "bot123456" not in text,
    )


def main() -> int:
    setup_console()

    # --- успешный ответ ---
    ok_body = json.dumps({"ok": True, "result": {"message_id": 7, "chat": {"id": FAKE_CHAT}}})
    with patch("urllib.request.urlopen", return_value=FakeResponse(200, ok_body)):
        result = send_message(FAKE_TOKEN, FAKE_CHAT, "привет")

    check("успешный ответ разобран", result.get("ok") is True)
    check("в ответе есть message_id", result.get("result", {}).get("message_id") == 7)

    # --- ok=false ---
    bad_body = json.dumps({"ok": False, "description": "chat not found"})
    with patch("urllib.request.urlopen", return_value=FakeResponse(200, bad_body)):
        try:
            send_message(FAKE_TOKEN, FAKE_CHAT, "привет")
            check("ok=false поднимает SendError", False, "ошибки не было")
        except SendError as error:
            expect_error("ok=false", ["ok=false", "chat not found"], error)

    # --- HTTP 401: недействительный токен ---
    body401 = json.dumps({"ok": False, "error_code": 401, "description": "Unauthorized"})
    error401 = urllib.error.HTTPError(
        "https://api.telegram.org/botXXX/sendMessage",
        401,
        "Unauthorized",
        {},
        io.BytesIO(body401.encode("utf-8")),
    )
    with patch("urllib.request.urlopen", side_effect=error401):
        try:
            send_message(FAKE_TOKEN, FAKE_CHAT, "привет")
            check("HTTP 401 поднимает SendError", False, "ошибки не было")
        except SendError as error:
            expect_error("HTTP 401", ["401", "токен"], error)

    # --- HTTP 429: слишком часто ---
    body429 = json.dumps(
        {
            "ok": False,
            "error_code": 429,
            "description": "Too Many Requests: retry after 12",
            "parameters": {"retry_after": 12},
        }
    )
    error429 = urllib.error.HTTPError(
        "https://api.telegram.org/botXXX/sendMessage",
        429,
        "Too Many Requests",
        {},
        io.BytesIO(body429.encode("utf-8")),
    )
    with patch("urllib.request.urlopen", side_effect=error429):
        try:
            send_message(FAKE_TOKEN, FAKE_CHAT, "привет")
            check("HTTP 429 поднимает SendError", False, "ошибки не было")
        except SendError as error:
            expect_error("HTTP 429", ["429", "подождать"], error)

    # --- HTTP 403: нет прав на публикацию ---
    error403 = urllib.error.HTTPError(
        "https://api.telegram.org/botXXX/sendMessage",
        403,
        "Forbidden",
        {},
        io.BytesIO(b'{"ok": false, "description": "bot is not a member"}'),
    )
    with patch("urllib.request.urlopen", side_effect=error403):
        try:
            send_message(FAKE_TOKEN, FAKE_CHAT, "привет")
            check("HTTP 403 поднимает SendError", False, "ошибки не было")
        except SendError as error:
            expect_error("HTTP 403", ["403", "прав"], error)

    # --- нет сети ---
    with patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError("connection refused"),
    ):
        try:
            send_message(FAKE_TOKEN, FAKE_CHAT, "привет")
            check("URLError поднимает SendError", False, "ошибки не было")
        except SendError as error:
            expect_error("нет сети", ["соединиться"], error)

    # --- оборванное соединение ---
    import http.client

    with patch(
        "urllib.request.urlopen",
        side_effect=http.client.IncompleteRead(b"part of response"),
    ):
        try:
            send_message(FAKE_TOKEN, FAKE_CHAT, "привет")
            check("IncompleteRead поднимает SendError", False, "ошибки не было")
        except SendError as error:
            expect_error("обрыв соединения", ["оборвалось"], error)

    # --- таймаут ---
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        try:
            send_message(FAKE_TOKEN, FAKE_CHAT, "привет")
            check("таймаут поднимает SendError", False, "ошибки не было")
        except SendError as error:
            expect_error("таймаут", ["не ответил"], error)

    # --- сброс соединения ---
    with patch("urllib.request.urlopen", side_effect=ConnectionResetError("reset")):
        try:
            send_message(FAKE_TOKEN, FAKE_CHAT, "привет")
            check("ConnectionResetError поднимает SendError", False, "ошибки не было")
        except SendError as error:
            expect_error("сброс соединения", ["Сетевая ошибка"], error)

    # --- не-JSON ответ ---
    with patch("urllib.request.urlopen", return_value=FakeResponse(200, "<html>проверка</html>")):
        try:
            send_message(FAKE_TOKEN, FAKE_CHAT, "привет")
            check("не-JSON поднимает SendError", False, "ошибки не было")
        except SendError as error:
            expect_error("не-JSON ответ", ["JSON"], error)

    print("-" * 50)
    if FAILURES:
        print(f"ПРОВАЛЕНО проверок: {len(FAILURES)}")
        return 1

    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
