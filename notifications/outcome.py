from dataclasses import dataclass


@dataclass
class Outcome:
    """Чем закончилась отправка одного сообщения."""

    ok: bool
    error: str = ""
    # Канал не настроен (нет токена): сообщение только записано в лог, повторять не нужно
    skipped: bool = False
