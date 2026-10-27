"""Офлайн-проверка логики сводки: разбор ответа, валидация, слияние, рендер.

Сеть не используется — модель не вызывается, данные чата не читаются.

Запуск:
    uv run python test_summary.py
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from telegramdigest.console import setup_console  # noqa: E402
from telegramdigest.summary import (  # noqa: E402
    ALLOWED_TITLES,
    collect_rows,
    merge_sections,
    normalize_title,
    parse_sections,
    render_summary,
    split_into_chunks,
    validate_sections,
)

FAILURES = []


def check(name, condition, detail=""):
    if condition:
        print(f"OK   {name}")
    else:
        print(f"FAIL {name} {detail}")
        FAILURES.append(name)


def main() -> int:
    setup_console()

    # --- normalize_title ---
    check(
        "точный заголовок остаётся как есть",
        normalize_title("Проблемы дома") == "Проблемы дома",
    )
    check(
        "похожий заголовок приводится к разрешённому",
        normalize_title("Отключения") == "Отключения и работы",
    )
    check(
        "чужой заголовок отклоняется",
        normalize_title("Что-то странное") is None,
    )
    check(
        "все разрешённые заголовки остаются разрешёнными",
        all(normalize_title(t) == t for t in ALLOWED_TITLES),
    )

    # --- parse_sections ---
    raw = '{"sections": [{"title": "Проблемы дома", "items": ["нет воды"]}]}'
    parsed = parse_sections(raw)
    check("JSON разбирается", parsed == [{"title": "Проблемы дома", "items": ["нет воды"]}])

    fenced = "```json\n" + raw + "\n```"
    check(
        "JSON в markdown-ограждении разбирается",
        parse_sections(fenced) == [{"title": "Проблемы дома", "items": ["нет воды"]}],
    )

    plain = parse_sections("Просто текст без структуры")
    check(
        "не-JSON сохраняется как есть, без выдумок",
        plain == [{"title": "Сводка", "items": ["Просто текст без структуры"]}],
    )

    empty_items = parse_sections('{"sections": [{"title": "Проблемы дома", "items": []}]}')
    check("раздел без пунктов отбрасывается", empty_items == [])

    # --- validate_sections ---
    good = [{"title": "Проблемы дома", "items": ["не работает насос в подъезде 2"]}]
    check("нормальная сводка проходит проверку", validate_sections(good) is None)

    template = [{"title": "Название раздела", "items": ["пункт 1", "пункт 2"]}]
    check(
        "шаблон из инструкции отклоняется",
        validate_sections(template) is not None,
    )

    wrong_title = [{"title": "Что-то странное", "items": ["не работает насос"]}]
    check(
        "чужой заголовок отклоняется",
        validate_sections(wrong_title) is not None,
    )

    check("пустая сводка отклоняется", validate_sections([]) is not None)

    # --- merge_sections ---
    merged = merge_sections(
        [
            [{"title": "Проблемы дома", "items": ["нет воды"]}],
            [
                {"title": "Проблемы дома", "items": ["нет воды", "не едет лифт"]},
                {"title": "Отключения и работы", "items": ["свет отключат в среду"]},
            ],
        ]
    )
    check(
        "одинаковые разделы сливаются, повтор пункта убирается",
        merged
        == [
            {"title": "Проблемы дома", "items": ["нет воды", "не едет лифт"]},
            {"title": "Отключения и работы", "items": ["свет отключат в среду"]},
        ],
        str(merged),
    )

    # --- collect_rows / split_into_chunks ---
    data = {
        "groups": [
            {
                "group_id": -100,
                "group_title": "Дом",
                "messages": [
                    {"date": "2026-10-19T10:00:00", "text": "нет воды"},
                    {"date": "2026-10-19T11:00:00", "text": "   "},
                ],
            }
        ]
    }
    rows = collect_rows(data)
    check("пустые сообщения отбрасываются", len(rows) == 1)

    long_rows = [
        {"date": "2026-10-19T10:00:00", "group": "Дом", "text": "а" * 500}
        for _ in range(50)
    ]
    chunks = split_into_chunks(long_rows)
    sizes = [sum(len(line) for line in chunk) for chunk in chunks]
    check(
        "части не превышают лимит по длине",
        all(size <= 12000 for size in sizes),
        str(sizes),
    )
    check(
        "все сообщения сохранены при нарезке",
        sum(len(chunk) for chunk in chunks) == 50,
    )
    check(
        "крупные объёмы делятся на несколько частей",
        len(chunks) > 1,
        f"{len(chunks)} частей",
    )

    # --- render_summary ---
    text = render_summary(
        {"sections": [{"title": "Проблемы дома", "items": ["нет воды"]}]},
        {"period_start": "2026-10-18T21:14:00", "period_hours": 24},
    )
    check("в тексте есть заголовок", "Домовой дайджест" in text)
    check("в тексте есть период", "Период: последние 24 ч." in text)
    check("в тексте есть пункт", "• нет воды" in text)

    print("-" * 50)
    if FAILURES:
        print(f"ПРОВАЛЕНО проверок: {len(FAILURES)}")
        return 1

    print("Все проверки пройдены.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
