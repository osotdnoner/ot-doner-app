"""
Напоминания управляющему и собственнику. Запускается по расписанию каждые 5 минут:

    python manage.py send_reminders

Что делает:
- просроченные задачи;
- смены, на которые никто не отметился;
- опоздания, о которых ещё не сообщили (администратор не указал причину за 10 минут);
- повторяет сообщения, которые не ушли из-за сбоя связи.

На сервере это делает cron (позже — Celery). Команду можно запускать сколько угодно
раз: каждое сообщение уходит только один раз.
"""

from datetime import timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import IntegrityError
from django.utils import timezone

from checklists.models import Task
from notifications.models import Notification
from notifications.service import notify, retry_unsent
from points.models import Point
from shifts.models import MissedCheckinAlert, Shift
from shifts.services import report_late, shift_slots_around


def local_time(dt, point) -> str:
    return f"{timezone.localtime(dt, ZoneInfo(point.timezone)):%H:%M}"


def notify_overdue_tasks(now) -> int:
    sent = 0
    overdue = Task.objects.filter(status=Task.TaskStatus.OPEN, deadline__lt=now, overdue_notified=False)
    for task in overdue.select_related("point", "responsible"):
        who = task.responsible or "ответственный не назначен"
        notify(
            task.point, Notification.Event.REMINDER, "Просрочена задача",
            f"Точка «{task.point}»: {task.title}\n"
            f"{who}, срок был {local_time(task.deadline, task.point)}.",
        )
        task.overdue_notified = True
        task.save(update_fields=["overdue_notified"])
        sent += 1
    return sent


def notify_missed_checkins(now) -> int:
    sent = 0
    delay = timedelta(minutes=settings.MISSED_CHECKIN_ALERT_MINUTES)
    for point in Point.objects.filter(is_active=True):
        for slot in shift_slots_around(point, now):
            if not (slot.start + delay <= now < slot.end):
                continue
            if Shift.objects.filter(point=point, planned_start=slot.start).exists():
                continue
            try:
                MissedCheckinAlert.objects.create(point=point, planned_start=slot.start)
            except IntegrityError:
                continue  # уже сообщали
            kind = dict(Shift.Kind.choices)[slot.kind].lower()
            notify(
                point, Notification.Event.REMINDER, "Никто не отметился на смену",
                f"Точка «{point}»: на {kind} смену ({local_time(slot.start, point)}) "
                f"никто из администраторов не отметился.",
            )
            sent += 1
    return sent


def notify_late_without_reason(now) -> int:
    """Опоздавший не указал причину — сообщаем всё равно, чтобы опоздание не потерялось."""
    waited = now - timedelta(minutes=settings.LATE_REASON_WAIT_MINUTES)
    shifts = Shift.objects.filter(
        late_minutes__gt=0, late_notified=False, late_reason="",
        checked_in_at__lte=waited, checked_in_at__gte=now - timedelta(hours=12),
    ).select_related("point", "admin")
    for shift in shifts:
        report_late(shift)
    return len(shifts)


class Command(BaseCommand):
    help = "Сообщает о просроченных задачах, сменах без отметки и опозданиях; повторяет неотправленное"

    def handle(self, *args, **options):
        now = timezone.now()
        tasks = notify_overdue_tasks(now)
        missed = notify_missed_checkins(now)
        late = notify_late_without_reason(now)
        retried = retry_unsent(now)
        self.stdout.write(
            f"Просроченных задач: {tasks}. Смен без отметки: {missed}. "
            f"Опозданий без причины: {late}. Повторно отправлено: {retried}."
        )
