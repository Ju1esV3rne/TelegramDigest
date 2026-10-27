"""Настройка вывода в консоль.

На Windows консоль по умолчанию использует cp1251/cp866, из-за чего
русский текст превращается в «кракозябры». Принудительно переключаем
stdout/stderr на UTF-8 с заменой непечатаемых символов.
"""

import sys


def setup_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                # Поток уже перенастроен или не поддерживает reconfigure.
                pass
