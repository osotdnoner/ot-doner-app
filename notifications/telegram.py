"""
Отправка в Telegram через бота.

Пока в .env нет TELEGRAM_BOT_TOKEN, сообщения не отправляются, а пишутся в лог
(их видно в окне, где запущен сервер). Ошибка Telegram никогда не ломает работу
приложения: отметка администратора сохраняется в любом случае.
"""

import logging

import requests
from django.conf import settings

from .outcome import Outcome

logger = logging.getLogger("otdoner.telegram")

# Понятные подсказки к частым ответам Telegram
HINTS = {
    "chat not found": "Проверьте chat ID и что человек написал боту /start.",
    "bot can't initiate conversation": "Человек должен сначала открыть бота и нажать «Старт».",
    "bot was blocked by the user": "Человек заблокировал бота — пусть разблокирует и нажмёт «Старт».",
    "Unauthorized": "Неверный TELEGRAM_BOT_TOKEN.",
}


def send_message(chat_id: str, text: str) -> Outcome:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        logger.info("[Telegram → %s] %s", chat_id, text)
        return Outcome(False, "Не задан TELEGRAM_BOT_TOKEN — сообщение только в логе сервера.", skipped=True)
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text[:4096]},
            timeout=10,
        )
    except requests.RequestException as exc:
        logger.warning("Нет связи с Telegram (chat_id=%s): %s", chat_id, exc)
        return Outcome(False, f"Нет связи с Telegram: {exc.__class__.__name__}")
    if response.ok:
        return Outcome(True)

    try:
        description = response.json().get("description", "")
    except ValueError:
        description = response.text[:200]
    hint = next((h for key, h in HINTS.items() if key in description), "")
    logger.warning("Telegram отказал (chat_id=%s): %s", chat_id, description)
    return Outcome(False, f"Telegram: {description}. {hint}".strip())
