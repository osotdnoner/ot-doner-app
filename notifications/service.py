"""
Кому и куда отправлять уведомления.

    notify(point, Notification.Event.LATE, "Опоздание администратора", "Точка «27» …")

Получатели — управляющие этой точки и собственники (собственник без точек получает по всем точкам),
у которых в панели включено это событие. Каждому сообщение уходит во все мессенджеры,
которые у него заполнены: Telegram chat ID и/или номер WhatsApp.

Сообщения сначала записываются в журнал, а отправляются в фоне, чтобы администратор
не ждал ответа Telegram и WhatsApp. Не ушедшие из-за сбоя связи повторяет send_reminders.
"""

import logging
import threading
from datetime import timedelta

from django.conf import settings
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

from . import telegram, whatsapp
from .models import Notification
from .outcome import Outcome

logger = logging.getLogger("otdoner.notifications")

Event = Notification.Event

# Какая галочка в карточке пользователя включает событие
PREFERENCE = {
    Event.LATE: "notify_late",
    Event.PROBLEMS: "notify_problems",
    Event.URGENT: "notify_urgent",
    Event.REMINDER: "notify_reminders",
}

MAX_ATTEMPTS = 3


def recipients_for(point, event: str):
    from accounts.models import User

    users = User.objects.filter(is_active=True, role__in=[User.Role.MANAGER, User.Role.OWNER])
    if event in PREFERENCE:
        users = users.filter(**{PREFERENCE[event]: True})
    if point is not None:
        users = users.filter(Q(points=point) | Q(role=User.Role.OWNER, points__isnull=True))
    return users.distinct().order_by("id")


def notify(point, event: str, title: str, text: str) -> int:
    """Ставит сообщение в очередь всем получателям. Возвращает, сколько сообщений поставлено."""
    queued = []
    recipients = list(recipients_for(point, event))
    for user in recipients:
        addresses = user.notification_addresses()
        if not addresses:
            Notification.objects.create(
                event=event, point=point, recipient=user, title=title, text=text, state=Notification.State.SKIPPED,
                error="У человека не указаны ни Telegram chat ID, ни номер WhatsApp.",
            )
            continue
        for channel, address in addresses:
            queued.append(Notification.objects.create(
                event=event, point=point, recipient=user, channel=channel, address=address, title=title, text=text,
            ))
    if not recipients:
        Notification.objects.create(
            event=event, point=point, title=title, text=text, state=Notification.State.SKIPPED,
            error="Некому отправить: у точки нет управляющего или собственника с включённым уведомлением.",
        )
        logger.info("[Некому отправить] %s\n%s", title, text)
    if queued:
        _start_delivery([n.id for n in queued])
    return len(queued)


def send_now(notification: Notification) -> Outcome:
    """Отправляет одно сообщение и записывает результат в журнал."""
    if notification.channel == Notification.Channel.TELEGRAM:
        outcome = telegram.send_message(notification.address, notification.full_text)
    elif notification.channel == Notification.Channel.WHATSAPP:
        outcome = whatsapp.send_message(notification.address, notification.title, notification.text)
    else:
        outcome = Outcome(False, "Не указан мессенджер.", skipped=True)

    notification.attempts += 1
    notification.error = outcome.error
    if outcome.ok:
        notification.state = Notification.State.SENT
        notification.sent_at = timezone.now()
    else:
        notification.state = Notification.State.SKIPPED if outcome.skipped else Notification.State.FAILED
    notification.save(update_fields=["attempts", "error", "state", "sent_at"])
    return outcome


def _deliver(ids: list[int]):
    for notification in Notification.objects.filter(id__in=ids, state=Notification.State.PENDING):
        try:
            send_now(notification)
        except Exception:  # одна неудача не должна мешать остальным сообщениям
            logger.exception("Не удалось отправить сообщение %s", notification.id)


def _deliver_in_background(ids: list[int]):
    try:
        _deliver(ids)
    finally:
        connection.close()  # у фонового потока своё подключение к базе


def _start_delivery(ids: list[int]):
    if not settings.NOTIFICATIONS_ASYNC:
        _deliver(ids)
        return
    # Отправляем после сохранения в базу, иначе фоновый поток может не увидеть записи
    transaction.on_commit(
        lambda: threading.Thread(target=_deliver_in_background, args=(ids,), daemon=True).start()
    )


def retry_unsent(now=None) -> int:
    """Повторяет сообщения, которые не ушли из-за сбоя связи (последние 2 часа, не больше 3 попыток)."""
    now = now or timezone.now()
    unsent = Notification.objects.filter(created_at__gte=now - timedelta(hours=2)).filter(
        Q(state=Notification.State.PENDING, created_at__lte=now - timedelta(minutes=1))
        | Q(state=Notification.State.FAILED, attempts__lt=MAX_ATTEMPTS)
    )
    count = 0
    for notification in unsent:
        send_now(notification)
        count += 1
    return count


def send_test(user, channels: list[str] | None = None) -> list[tuple[str, Outcome]]:
    """Тестовое сообщение человеку во все его мессенджеры (или только в указанные)."""
    results = []
    for channel, address in user.notification_addresses():
        if channels and channel not in channels:
            continue
        notification = Notification.objects.create(
            event=Event.TEST, recipient=user, channel=channel, address=address,
            title="Проверка связи",
            text=f"Здравствуйте, {user.get_short_name() or user}! Уведомления «ОТ Донер» будут приходить сюда.",
        )
        results.append((notification.get_channel_display(), send_now(notification)))
    return results
