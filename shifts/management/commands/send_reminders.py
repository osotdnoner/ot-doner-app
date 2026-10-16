"""
Напоминания управляющему. Запускается по расписанию каждые 5 минут:

    python manage.py send_reminders

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
from notifications.telegram import notify_point_managers
from points.models import Point
from shifts.models import MissedCheckinAlert, Shift
from shifts.services import shift_slots_around


def local_time(dt, point) -> str:
    return f"{timezone.localtime(dt, ZoneInfo(point.timezone)):%H:%M}"


def notify_overdue_tasks(now) -> int:
    sent = 0
    overdue = Task.objects.filter(status=Task.TaskStatus.OPEN, deadline__lt=now, overdue_notified=False)
    for task in overdue.select_related("point", "responsible"):
        who = task.responsible or "ответственный не назначен"
        notify_point_managers(
            task.point,
            f"Просрочена задача · Точка «{task.point}»\n{task.title}\n"
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
            notify_point_managers(
                point,
                f"Точка «{point}»: на {kind} смену ({local_time(slot.start, point)}) "
                f"никто из администраторов не отметился.",
            )
            sent += 1
    return sent


class Command(BaseCommand):
    help = "Сообщает управляющим о просроченных задачах и сменах без отметки администратора"

    def handle(self, *args, **options):
        now = timezone.now()
        tasks = notify_overdue_tasks(now)
        missed = notify_missed_checkins(now)
        self.stdout.write(f"Просроченных задач: {tasks}. Смен без отметки: {missed}.")
