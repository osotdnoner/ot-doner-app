"""
Правила зарплатного учёта смены: кто вышел, опоздания, штрафы, бонусы.

Администратор после отметки «Я пришёл» отмечает сотрудников: пришёл вовремя, опоздал
(во сколько пришёл и почему) или не вышел (почему). Штраф считается сам по ступеням
из панели. Бонус администратор ставит сам: сумма и за что.
Каждое изменение сразу уходит в Google Таблицу (см. sheets.py).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.utils import timezone

from notifications.models import Notification
from notifications.service import notify
from staff.models import Employee

from . import sheets
from .models import Attendance, Bonus, FineRules

MAX_BONUS = 1_000_000


class PayrollError(Exception):
    """Понятная администратору ошибка: что исправить."""


def local(dt: datetime, point) -> datetime:
    return timezone.localtime(dt, ZoneInfo(point.timezone))


def employees_for(shift):
    """Кого отмечает администратор: сотрудники точки, кроме администраторов."""
    return (
        Employee.objects.filter(point=shift.point, is_active=True)
        .exclude(position=Employee.Position.ADMIN)
        .order_by("position", "full_name")
    )


def marked_count(shift) -> tuple[int, int]:
    employees = employees_for(shift)
    done = Attendance.objects.filter(shift=shift, employee__in=employees).count()
    return done, employees.count()


def late_minutes_for(shift, arrived_at: datetime) -> int:
    minutes = int((arrived_at - shift.planned_start).total_seconds() // 60)
    return minutes if minutes > shift.point.late_grace_minutes else 0


def parse_arrival(shift, value: str, now: datetime) -> datetime:
    """'10:25' → время прихода в день смены (у ночной смены после полуночи — следующий день)."""
    try:
        hours, minutes = (int(part) for part in value.strip().split(":")[:2])
        start = local(shift.planned_start, shift.point)
        arrived = start.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    except (ValueError, AttributeError):
        raise PayrollError("Укажите, во сколько сотрудник пришёл, например 10:25.")
    if arrived < start - timedelta(hours=6):
        arrived += timedelta(days=1)
    if arrived > now + timedelta(minutes=2):
        raise PayrollError("Время прихода не может быть позже, чем сейчас.")
    return arrived


@transaction.atomic
def mark(shift, employee: Employee, status: str, *, user, arrived: str = "", reason: str = "",
         now: datetime | None = None) -> Attendance:
    now = now or timezone.now()
    if employee.point_id != shift.point_id or employee.position == Employee.Position.ADMIN:
        raise PayrollError("Этого сотрудника нет в списке точки.")
    if status not in Attendance.Status.values:
        raise PayrollError("Выберите: пришёл вовремя, опоздал или не вышел.")
    reason = reason.strip()
    rules = FineRules.current()
    attendance = Attendance.objects.filter(shift=shift, employee=employee).first() or Attendance(
        shift=shift, employee=employee,
    )
    was_absent = attendance.pk and attendance.status == Attendance.Status.ABSENT

    if status == Attendance.Status.ON_TIME:
        arrived_at, minutes, reason, fine = None, 0, "", 0
    elif status == Attendance.Status.LATE:
        arrived_at = parse_arrival(shift, arrived, now)
        minutes = late_minutes_for(shift, arrived_at)
        if not minutes:
            raise PayrollError(f"По этому времени опоздания нет: смена началась в "
                               f"{local(shift.planned_start, shift.point):%H:%M}. Отметьте «Вовремя».")
        if not reason:
            raise PayrollError("Напишите причину опоздания.")
        fine = rules.late_fine(minutes)
    else:
        if not reason:
            raise PayrollError("Напишите, почему сотрудник не вышел.")
        if attendance.pk and attendance.bonuses.exists():
            raise PayrollError("У сотрудника есть бонус за эту смену. Сначала удалите бонус.")
        arrived_at, minutes, fine = None, 0, rules.absence_fine

    attendance.status = status
    attendance.arrived_at = arrived_at
    attendance.late_minutes = minutes
    attendance.reason = reason
    attendance.fine, attendance.fine_manual, attendance.fine_note = fine, False, ""
    attendance.marked_by = user
    attendance.needs_sync = True
    attendance.save()
    sheets.queue([attendance.id])

    if status == Attendance.Status.ABSENT and not was_absent:
        notify(
            shift.point, Notification.Event.URGENT, "Сотрудник не вышел на смену",
            f"Точка «{shift.point}»: {employee} ({employee.get_position_display().lower()}).\n"
            f"Причина: {reason}\nАдминистратор: {user}.",
        )
    return attendance


def add_bonus(attendance: Attendance, *, amount: str | int, reason: str, user) -> Bonus:
    try:
        amount = int(str(amount).replace(" ", "").replace(" ", ""))
    except ValueError:
        raise PayrollError("Сумма бонуса — целое число в тенге, например 2000.")
    if not 0 < amount <= MAX_BONUS:
        raise PayrollError("Сумма бонуса должна быть больше нуля.")
    reason = reason.strip()
    if not reason:
        raise PayrollError("Напишите, за что бонус.")
    if attendance.status == Attendance.Status.ABSENT:
        raise PayrollError("Сотрудник не вышел на смену — бонус поставить нельзя.")
    bonus = Bonus.objects.create(attendance=attendance, amount=amount, reason=reason[:200], created_by=user)
    resync(attendance)
    return bonus


def remove_bonus(bonus: Bonus):
    attendance = bonus.attendance
    bonus.delete()
    resync(attendance)


def resync(attendance: Attendance):
    Attendance.objects.filter(pk=attendance.pk).update(needs_sync=True, marked_at=timezone.now())
    sheets.queue([attendance.pk])


def record_admin(shift) -> Attendance:
    """Приход администратора (по кнопке «Я пришёл») тоже попадает в зарплату."""
    admin = shift.admin
    employee = Employee.objects.filter(user=admin).first()
    if employee is None:
        employee = Employee.objects.create(
            user=admin, point=shift.point, position=Employee.Position.ADMIN,
            full_name=admin.get_full_name() or admin.username, phone=admin.username,
        )
    rules = FineRules.current()
    attendance = Attendance.objects.filter(shift=shift, employee=employee).first() or Attendance(
        shift=shift, employee=employee,
    )
    attendance.status = Attendance.Status.LATE if shift.late_minutes else Attendance.Status.ON_TIME
    attendance.arrived_at = shift.checked_in_at
    attendance.late_minutes = shift.late_minutes
    attendance.reason = shift.late_reason
    if not attendance.fine_manual:
        attendance.fine = rules.late_fine(shift.late_minutes) if rules.fine_admins else 0
    attendance.marked_by = admin
    attendance.needs_sync = True
    attendance.save()
    sheets.queue([attendance.id])
    return attendance
