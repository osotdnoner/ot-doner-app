"""
Уведомления в Telegram.

Пока в .env нет TELEGRAM_BOT_TOKEN, сообщения не отправляются, а пишутся в лог
(их видно в окне, где запущен сервер). Ошибка Telegram никогда не ломает работу
приложения: отметка администратора сохраняется в любом случае.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger("otdoner.telegram")


def send_message(chat_id: str, text: str) -> bool:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        logger.info("[Telegram → %s] %s", chat_id or "получатель не указан", text)
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=5,
        )
        response.raise_for_status()
        return True
    except requests.RequestException:
        logger.exception("Не удалось отправить сообщение в Telegram (chat_id=%s)", chat_id)
        return False


def notify_point_managers(point, text: str) -> int:
    """Отправляет сообщение управляющим точки. Возвращает, скольким ушло."""
    from accounts.models import User

    managers = User.objects.filter(points=point, role=User.Role.MANAGER, is_active=True)
    sent = 0
    for manager in managers:
        sent += send_message(manager.telegram_chat_id, text)
    if not managers:
        logger.info("[Telegram] У точки «%s» нет управляющего. Сообщение: %s", point, text)
    return sent
