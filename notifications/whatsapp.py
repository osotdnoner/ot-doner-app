"""
Отправка в WhatsApp через официальный WhatsApp Business Platform (Cloud API от Meta).

Первым написать человеку в WhatsApp компания может только заранее одобренным шаблоном.
Шаблон создаётся один раз в WhatsApp Manager (как — в README, раздел «WhatsApp»):

    имя: otdoner_alert, категория: Utility, язык: Russian (ru)
    текст: ОТ Донер: {{1}}

           {{2}}

           Подробности в приложении.

Приложение подставляет в {{1}} заголовок («Опоздание администратора»), в {{2}} — подробности.
Если в .env поставить WHATSAPP_TEMPLATE= (пусто), уходит обычный текст: так можно проверить связь,
но WhatsApp доставит его, только если получатель сам писал на номер компании за последние 24 часа.

Пока не заданы WHATSAPP_TOKEN и WHATSAPP_PHONE_NUMBER_ID, сообщения пишутся в лог.
"""

import logging
import re

import requests
from django.conf import settings

from .outcome import Outcome

logger = logging.getLogger("otdoner.whatsapp")

# Понятные подсказки к частым кодам ошибок WhatsApp Cloud API
HINTS = {
    190: "Токен недействителен или истёк — создайте постоянный токен системного пользователя.",
    100: "Проверьте WHATSAPP_PHONE_NUMBER_ID и данные шаблона.",
    131030: "Номер не в списке получателей тестового номера: WhatsApp Manager → API Setup → поле «To» → добавьте номер.",
    131047: "Получатель не писал на номер компании 24 часа — нужен одобренный шаблон (WHATSAPP_TEMPLATE).",
    132000: "В шаблоне должно быть ровно две переменные: {{1}} и {{2}}.",
    132001: "Шаблон с таким именем и языком не найден или ещё не одобрен.",
    132018: "Недопустимые символы в тексте шаблона.",
}


def normalize_phone(raw: str) -> str:
    """'8 701 123-45-67', '+7 (701) 1234567' → '77011234567' (так номер нужен WhatsApp)."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    elif len(digits) == 10:
        digits = "7" + digits
    return digits


def one_line(text: str, limit: int) -> str:
    """В переменных шаблона WhatsApp нельзя переносы строк, табуляцию и больше 4 пробелов подряд."""
    text = " · ".join(line.strip(" •\t") for line in text.splitlines() if line.strip(" •\t"))
    text = re.sub(r"\s{2,}", " ", text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def build_payload(phone: str, title: str, text: str) -> dict:
    base = {"messaging_product": "whatsapp", "recipient_type": "individual", "to": normalize_phone(phone)}
    template = settings.WHATSAPP_TEMPLATE
    if not template:
        return base | {"type": "text", "text": {"body": f"{title}\n{text}"[:4096]}}
    return base | {
        "type": "template",
        "template": {
            "name": template,
            "language": {"code": settings.WHATSAPP_TEMPLATE_LANG},
            "components": [{
                "type": "body",
                "parameters": [
                    {"type": "text", "text": one_line(title, 60)},
                    {"type": "text", "text": one_line(text, 900)},
                ],
            }],
        },
    }


def send_message(phone: str, title: str, text: str) -> Outcome:
    token, phone_number_id = settings.WHATSAPP_TOKEN, settings.WHATSAPP_PHONE_NUMBER_ID
    if not token or not phone_number_id:
        logger.info("[WhatsApp → %s] %s\n%s", phone, title, text)
        return Outcome(
            False, "Не заданы WHATSAPP_TOKEN и WHATSAPP_PHONE_NUMBER_ID — сообщение только в логе сервера.",
            skipped=True,
        )
    url = f"https://graph.facebook.com/{settings.WHATSAPP_API_VERSION}/{phone_number_id}/messages"
    try:
        response = requests.post(
            url, json=build_payload(phone, title, text), timeout=10,
            headers={"Authorization": f"Bearer {token}"},
        )
    except requests.RequestException as exc:
        logger.warning("Нет связи с WhatsApp (%s): %s", phone, exc)
        return Outcome(False, f"Нет связи с WhatsApp: {exc.__class__.__name__}")
    if response.ok:
        return Outcome(True)

    try:
        error = response.json().get("error", {})
    except ValueError:
        error = {"message": response.text[:200]}
    code = error.get("code")
    details = (error.get("error_data") or {}).get("details") or error.get("message", "")
    logger.warning("WhatsApp отказал (%s): %s %s", phone, code, details)
    return Outcome(False, f"WhatsApp ({code}): {details}. {HINTS.get(code, '')}".strip())
