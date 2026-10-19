"""
Отправка в Telegram через бота.

Пока в .env нет TELEGRAM_BOT_TOKEN, сообщения не отправляются, а пишутся в лог
(их видно в окне, где запущен сервер). Ошибка Telegram никогда не ломает работу
приложения: отметка администратора сохраняется в любом случае.
"""

import logging

import requests
from django.conf import settings
from django.utils.crypto import salted_hmac

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


# --- Подключение человека к боту без поиска chat ID ------------------------
#
# Человек открывает ссылку t.me/<бот>?start=<код>, нажимает «Старт», и Telegram передаёт боту
# сообщение «/start <код>». По коду приложение понимает, чей это чат, и само запоминает chat ID.


class TelegramError(Exception):
    """Понятное объяснение, почему не получилось связаться с ботом."""


def _call(method: str, **params) -> dict | list:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise TelegramError("Бот не настроен: на сервере не задан TELEGRAM_BOT_TOKEN.")
    try:
        response = requests.post(f"https://api.telegram.org/bot{token}/{method}", json=params, timeout=10)
    except requests.RequestException as exc:
        raise TelegramError(f"Нет связи с Telegram: {exc.__class__.__name__}. Попробуйте ещё раз.") from exc
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code == 401 or data.get("error_code") == 401:
        raise TelegramError("Telegram не принял токен бота. Проверьте TELEGRAM_BOT_TOKEN: его нужно скопировать целиком.")
    if not data.get("ok"):
        raise TelegramError(f"Telegram ответил ошибкой: {data.get('description') or response.status_code}.")
    return data["result"]


_usernames: dict[str, str] = {}


def bot_username() -> str:
    """Имя бота, например otdoner_alerts_bot (запоминается, пока не сменится токен)."""
    token = settings.TELEGRAM_BOT_TOKEN
    if token not in _usernames:
        _usernames[token] = _call("getMe")["username"]
    return _usernames[token]


def link_code(user) -> str:
    """Код для ссылки «Старт»: свой у каждого человека, подделать нельзя."""
    return f"{user.pk}_{salted_hmac('telegram-link', str(user.pk)).hexdigest()[:16]}"


def connect_link(user) -> str:
    return f"https://t.me/{bot_username()}?start={link_code(user)}"


def find_chat_id(user) -> str | None:
    """Ищет среди новых сообщений боту «/start <код этого человека>» и возвращает chat ID."""
    expected = f"/start {link_code(user)}"
    offset = None
    for _ in range(5):  # за раз Telegram отдаёт до 100 сообщений
        params = {"timeout": 0, "allowed_updates": ["message"]}
        if offset:
            params["offset"] = offset
        updates = _call("getUpdates", **params)
        for update in updates:
            message = update.get("message") or {}
            if (message.get("text") or "").strip() == expected:
                return str(message["chat"]["id"])
        if len(updates) < 100:
            return None
        offset = updates[-1]["update_id"] + 1
    return None
