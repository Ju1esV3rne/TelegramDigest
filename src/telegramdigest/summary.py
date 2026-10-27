"""Генерация сводки по сообщениям домового чата через OpenRouter.

Модель получает сообщения из data/messages.json и возвращает структурированную
сводку: список разделов, в каждом — список пунктов. Разделы, для которых в
сообщениях нет информации, в результат не попадают.

Используется совместимый с OpenAI клиент (пакет openai уже в зависимостях),
адрес OpenRouter — https://openrouter.ai/api/v1.

Главное правило модуля: в канал не должно уйти ничего, что модель выдумала или
не довела до конца. Поэтому ответ проверяется, а несоответствие приводит к
повторному запросу, а не к публикации.
"""

import json
import re

from openai import OpenAI

# OpenRouter говорит на том же протоколе, что и OpenAI, — меняется только адрес.
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Сколько символов из одного сообщения отправляем модели.
# Длинные простыни обрезаем, чтобы одно сообщение не перевесило всю сводку.
MAX_MESSAGE_CHARS = 1200

# Сколько символов сообщений помещается в один запрос к модели.
MAX_CHUNK_CHARS = 12000

REQUEST_TIMEOUT = 120.0

# Лимит токенов на ответ. Модели через openrouter/free тратят на ответ
# 2000-3000 токенов, и при лимите 3000 часть запросов обрывалась пустым
# ответом (finish_reason=length). Запас нужен.
MAX_TOKENS = 8000

# Сколько раз повторяем запрос, если ответ не прошёл проверку.
MAX_ATTEMPTS = 3

# Разрешённые заголовки разделов. Если модель пишет похожий, но не совпадающий
# заголовок, он приводится к одному из этих (см. normalize_title).
ALLOWED_TITLES = (
    "Отключения и работы",
    "Проблемы дома",
    "Заявки и вопросы жильцов",
    "Решения и подтверждения",
    "Требует внимания",
)

# Слова-заглушки: модель иногда возвращает шаблон из инструкции вместо сводки.
PLACEHOLDER_PATTERN = re.compile(
    r"(пункт\s*\d|название раздела|заголовок раздела|текст пункта|item\s*\d|"
    r"lorem|ipsum|пример|здесь текст|ваш текст|своими словами|\.\.\.)",
    re.IGNORECASE,
)

SYSTEM_PROMPT = """\
Ты — помощник домового чата жильцов. Твоя задача — составить краткую сводку
по сообщениям, которые тебе дали, и ничего больше.

Правила:
1. Используй ТОЛЬКО сведения из сообщений. Не добавляй причин, дат, времени,
   адресов, сумм, номеров квартир и решений, которых в сообщениях нет.
2. Если сообщение — жалоба, так и напиши: это жалоба, а не подтверждённый факт.
   Если подтверждение есть — отметь это отдельно.
3. Не выдумывай. Лучше не написать ничего, чем написать неправду.
4. Объединяй повторы одного и того же события в один пункт.
5. Выбрасывай эмоциональный шум и сообщения без полезной информации.
6. Сохраняй существенные детали: даты, сроки, номера подъездов, названия служб.
7. Пиши по-русски, кратко и по делу.
8. Каждый пункт — законченная фраза с конкретным содержанием из сообщений.
   Заглушек вида «пункт 1», «пример», «описание проблемы» быть не должно.

Формат ответа — строго JSON без пояснений и без markdown:
{
  "sections": [
    {
      "title": "Проблемы дома",
      "items": ["На втором подъезде не работает домофон, заявка от 12 июня"]
    },
    {
      "title": "Решения и подтверждения",
      "items": ["УК подтвердила замену насоса, срок — до конца недели"]
    }
  ]
}

Заголовки разделов выбирай ТОЛЬКО из этого списка:
- "Отключения и работы"
- "Проблемы дома"
- "Заявки и вопросы жильцов"
- "Решения и подтверждения"
- "Требует внимания"

Не добавляй раздел, если под него не нашлось ни одного пункта.
"""


class SummaryError(RuntimeError):
    """Не удалось получить сводку."""


def collect_rows(data: dict) -> list[dict]:
    """Превращает содержимое messages.json в плоский список сообщений."""
    rows = []

    for group in data.get("groups", []):
        title = group.get("group_title") or str(group.get("group_id", "?"))

        for message in group.get("messages", []):
            text = (message.get("text") or "").strip()
            if not text:
                continue

            if len(text) > MAX_MESSAGE_CHARS:
                text = text[:MAX_MESSAGE_CHARS] + "…"

            rows.append(
                {
                    "date": (message.get("date") or "")[:16],
                    "group": title,
                    "text": text,
                }
            )

    return rows


def split_into_chunks(rows: list[dict], max_chars: int = MAX_CHUNK_CHARS) -> list[list[str]]:
    """Делит сообщения на части, чтобы запрос не превышал лимит.

    Возвращает список частей, каждая — готовый текст для модели.
    """
    chunks = []
    current = []
    current_len = 0

    for row in rows:
        line = f"[{row['date']}] {row['group']}: {row['text']}"

        if current and current_len + len(line) > max_chars:
            chunks.append(current)
            current = []
            current_len = 0

        current.append(line)
        current_len += len(line) + 1

    if current:
        chunks.append(current)

    return chunks


def build_prompt(lines: list[str]) -> str:
    """Собирает текст запроса из части сообщений."""
    return (
        "Сообщения из домового чата, в хронологическом порядке:\n\n" + "\n".join(lines)
    )


def normalize_title(title: str) -> str | None:
    """Приводит заголовок раздела к одному из разрешённых.

    Возвращает None, если заголовок не похож ни на один разрешённый.
    """
    cleaned = title.strip().lower()

    for allowed in ALLOWED_TITLES:
        if cleaned == allowed.lower():
            return allowed

    # Похожие формулировки: «Отключения», «Проблемы в доме», «Требует внимания!»
    keywords = {
        "отключ": "Отключения и работы",
        "работ": "Отключения и работы",
        "проблем": "Проблемы дома",
        "авари": "Проблемы дома",
        "заявк": "Заявки и вопросы жильцов",
        "вопрос": "Заявки и вопросы жильцов",
        "решени": "Решения и подтверждения",
        "подтвержд": "Решения и подтверждения",
        "внимани": "Требует внимания",
    }

    for word, allowed in keywords.items():
        if word in cleaned:
            return allowed

    return None


def validate_sections(sections: list[dict]) -> str | None:
    """Проверяет сводку. Возвращает описание проблемы или None, если всё в порядке.

    Проверяются три вещи: заголовки из разрешённого списка, отсутствие
    шаблонных заглушек и наличие хотя бы одного содержательного пункта.
    """
    if not sections:
        return "модель не вернула ни одного раздела"

    for section in sections:
        title = section.get("title", "")
        items = section.get("items", [])

        if normalize_title(title) is None:
            return f"заголовок раздела «{title}» не из разрешённого списка"

        for item in items:
            if PLACEHOLDER_PATTERN.search(item):
                return "пункт выглядит как шаблонная заглушка, а не как факт из сообщений"

    return None


def _extract_json(raw: str) -> dict | None:
    """Достаёт JSON из ответа модели.

    Модель может обернуть JSON в ```json ... ```, поэтому сначала убираем
    ограждение, потом ищем фигурные скобки.
    """
    text = raw.strip()

    if "```" in text:
        # Берём кусок между первой и последней парой скобок внутри блока.
        parts = text.split("```")
        for part in parts:
            if "{" in part and "}" in part:
                text = part
                break

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None

    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None

    return data if isinstance(data, dict) else None


def parse_sections(raw: str) -> list[dict]:
    """Разбирает ответ модели в список разделов.

    Если модель не вернула ожидаемый JSON, её текст сохраняется как один
    пункт в разделе "Сводка" — так в канал попадёт то, что модель написала
    на самом деле, без выдуманных данных.
    """
    data = _extract_json(raw)

    if data is None:
        return [{"title": "Сводка", "items": [raw]}]

    sections = data.get("sections")
    if not isinstance(sections, list):
        return [{"title": "Сводка", "items": [raw]}]

    result = []
    for section in sections:
        if not isinstance(section, dict):
            continue

        title = str(section.get("title") or "").strip()
        items = section.get("items")
        if not title or not isinstance(items, list):
            continue

        clean = [str(item).strip() for item in items if str(item).strip()]
        if clean:
            result.append({"title": title, "items": clean})

    return result


def merge_sections(parts: list[list[dict]]) -> list[dict]:
    """Объединяет разделы из нескольких частей: одинаковые заголовки сливаются."""
    merged: dict[str, list[str]] = {}
    order: list[str] = []

    for part in parts:
        for section in part:
            title = normalize_title(section["title"]) or section["title"]
            if title not in merged:
                merged[title] = []
                order.append(title)
            for item in section["items"]:
                if item not in merged[title]:
                    merged[title].append(item)

    return [{"title": title, "items": merged[title]} for title in order]


def request_sections(
    prompt: str, api_key: str, model: str, max_attempts: int = MAX_ATTEMPTS
) -> list[dict]:
    """Просит модель вернуть сводку и проверяет ответ.

    openrouter/free выбирает бесплатную модель случайно, и модель может
    вернуть пустой ответ, шаблон из инструкции или неразбираемый текст.
    В таких случаях запрос повторяется с подсказкой, что именно не так.
    """
    client = OpenAI(
        api_key=api_key,
        base_url=OPENROUTER_BASE_URL,
        timeout=REQUEST_TIMEOUT,
        max_retries=2,
    )

    problem = ""

    for attempt in range(1, max_attempts + 1):
        user_prompt = prompt

        if problem:
            user_prompt += (
                "\n\nВАЖНО: предыдущий твой ответ не подошёл — "
                f"{problem}. Ответь заново: строго JSON, реальные пункты из "
                "сообщений, никаких шаблонных заглушек."
            )

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=MAX_TOKENS,
            temperature=0.3,
        )

        choice = response.choices[0]
        content = (choice.message.content or "").strip()

        if not content:
            # Модель исчерпала лимит токенов, не отдав ни слова.
            usage = getattr(response, "usage", None)
            completion = getattr(usage, "completion_tokens", "?")
            problem = (
                f"ответ оказался пустым (finish_reason={choice.finish_reason}, "
                f"completion_tokens={completion})"
            )
            print(f"Попытка {attempt}/{max_attempts}: {problem}. Повторяю...")
            continue

        sections = parse_sections(content)
        sections = [
            {"title": normalize_title(section["title"]) or section["title"], "items": section["items"]}
            for section in sections
        ]

        issue = validate_sections(sections)
        if issue is None:
            return sections

        problem = issue
        print(f"Попытка {attempt}/{max_attempts}: {issue}. Повторяю...")

    raise SummaryError(
        f"Модель не вернула корректную сводку за {max_attempts} попыток "
        f"(последняя проблема: {problem})."
    )


def generate_summary(data: dict, api_key: str, model: str) -> dict:
    """Готовит сводку по данным из messages.json.

    Возвращает {"sections": [...], "messages": N, "chunks": N}.
    """
    rows = collect_rows(data)
    if not rows:
        raise SummaryError("В собранных сообщениях нет текста — сводку строить не из чего.")

    chunks = split_into_chunks(rows)

    parts = []
    for lines in chunks:
        parts.append(request_sections(build_prompt(lines), api_key, model))

    sections = merge_sections(parts)
    if not sections:
        raise SummaryError("Модель не вернула ни одного раздела сводки.")

    return {"sections": sections, "messages": len(rows), "chunks": len(chunks)}


def render_summary(summary: dict, data: dict | None = None) -> str:
    """Собирает текст сводки для отправки в Telegram."""
    lines = ["Домовой дайджест", ""]

    if data:
        start = (data.get("period_start") or "")[:16].replace("T", " ")
        hours = data.get("period_hours")
        if start and hours:
            lines.append(f"Период: последние {hours} ч., с {start} UTC")
            lines.append("")

    for section in summary["sections"]:
        lines.append(section["title"])
        for item in section["items"]:
            lines.append(f"• {item}")
        lines.append("")

    return "\n".join(lines).strip()
