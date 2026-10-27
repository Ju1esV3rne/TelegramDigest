"""Отправка сообщений в Telegram через Bot API.

HTTP-логика вынесена из test_bot.py, чтобы сборщик, генератор и публикатор
использовали один и тот же код отправки.

Токен бота передаётся параметром и нигде не печатается: в URL он присутствует
как часть пути, поэтому полный URL в логи не выводится.
"""

import http.client
import json
import urllib.error
import urllib.request

# Таймаут HTTP-запроса в секундах.
REQUEST_TIMEOUT = 30

# Короткие пояснения к кодам ответов Telegram.
HTTP_HINTS = {
    400: "Telegram отклонил запрос: проверьте текст сообщения и ID канала.",
    401: "Токен бота недействителен — получите новый у @BotFather.",
    403: "У бота нет прав публиковать в этом канале.",
    404: "Канал не найден — проверьте TELEGRAM_TARGET_CHAT_ID.",
    429: "Слишком часто отправляем — Telegram просит подождать (в ответе есть retry_after).",
    500: "Ошибка на стороне Telegram, попробуйте позже.",
    502: "Telegram временно недоступен, попробуйте позже.",
}


class SendError(RuntimeError):
    """Не удалось отправить сообщение."""


def _describe_http_error(code: int, detail: str) -> str:
    """Понятное описание ошибки Telegram по HTTP-коду."""
    text = f"Telegram вернул HTTP {code}."

    hint = HTTP_HINTS.get(code)
    if hint:
        text += f" {hint}"

    if detail:
        # Тело ответа Telegram содержит только описание ошибки, но может быть
        # длинным — обрезаем, чтобы сообщение оставалось читаемым.
        text += f" Ответ: {detail[:300]}"

    return text


def send_message(token: str, chat_id: str, text: str) -> dict:
    """Отправляет одно сообщение через Bot API.

    Возвращает разобранный JSON-ответ Telegram.
    Бросает SendError с понятным описанием при любой неудаче.
    """
    # Токен — часть URL, поэтому сам URL никогда не печатаем.
    url = f"https://api.telegram.org/bot{token}/sendMessage"

    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status = response.status
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        # В str(error) входит полный URL с токеном — поэтому не печатаем его целиком.
        # Тело ответа Telegram безопасно: в нём только описание ошибки.
        try:
            detail = error.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — тело ответа может не читаться
            detail = ""

        raise SendError(_describe_http_error(error.code, detail.strip())) from None
    except urllib.error.URLError as error:
        raise SendError(f"Не удалось соединиться с Telegram — {error.reason}") from None
    except http.client.HTTPException as error:
        # Обрыв соединения или битый ответ: urllib не превращает такие
        # ошибки в URLError, поэтому ловим их отдельно.
        raise SendError(
            f"Соединение с Telegram оборвалось ({type(error).__name__})."
        ) from None
    except TimeoutError:
        raise SendError(f"Telegram не ответил за {REQUEST_TIMEOUT} сек.") from None
    except OSError as error:
        raise SendError(
            f"Сетевая ошибка при обращении к Telegram — {type(error).__name__}: {error}."
        ) from None

    if status != 200:
        raise SendError(f"Неожиданный HTTP-код {status} от Telegram.")

    try:
        result = json.loads(body)
    except json.JSONDecodeError:
        raise SendError("Ответ Telegram не является корректным JSON.") from None

    if not result.get("ok"):
        description = result.get("description", "без описания")
        raise SendError(f"Telegram вернул ok=false. Описание: {description}")

    return result
