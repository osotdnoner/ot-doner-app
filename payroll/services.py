"""
Правила зарплатного учёта смены: кто вышел, опоздания, штрафы, бонусы.

Администратор после отметки «Я пришёл» отмечает сотрудников: пришёл вовремя, опоздал
(во сколько пришёл и почему), не вышел (почему) или выходной (не его смена).
Дневной администратор может отметить полную смену — до конца ночной; ночной администратор
видит таких сотрудников уже отмеченными. Штраф считается сам по ступеням из панели.
Бонус, штраф во время смены и аванс администратор ставит сам: сумма и за что.
Переработку и недоработку — в часах. Если сменщик опоздал и кто-то задержался за него, одно
нажатие ставит переработку задержавшемуся и недоработку опоздавшему. Деньги за часы считает таблица.
Если кто-то не вышел, админ вызывает замену: человека с другого филиала или из резерва (выручалку).
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
from .models import Advance, Attendance, Bonus, ExtraHours, FineRules, Penalty

MAX_AMOUNT = 1_000_000
MAX_HOURS = 12


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


def full_from_day(shift) -> dict[int, Attendance]:
    """Для ночной смены: кто с дневной смены остался на полную. {id сотрудника: отметка}."""
    if shift.kind != shift.Kind.NIGHT:
        return {}
    records = Attendance.objects.filter(
        shift__point=shift.point, shift__kind=shift.Kind.DAY, kind=Attendance.Kind.FULL,
        status__in=Attendance.WORKED,
        shift__planned_start__gte=shift.planned_start - timedelta(hours=24),
        shift__planned_start__lt=shift.planned_start,
    ).select_related("shift__admin")
    return {a.employee_id: a for a in records}


def marked_count(shift) -> tuple[int, int]:
    employees = employees_for(shift)
    ids = set(Attendance.objects.filter(shift=shift, employee__in=employees).values_list("employee_id", flat=True))
    ids |= set(full_from_day(shift))
    return len(ids & set(employees.values_list("id", flat=True))), employees.count()


def can_be_full(shift) -> bool:
    return shift.kind == shift.Kind.DAY and shift.point.has_night_shift


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
         full: bool = False, now: datetime | None = None) -> Attendance:
    now = now or timezone.now()
    if employee.point_id != shift.point_id or employee.position == Employee.Position.ADMIN:
        raise PayrollError("Этого сотрудника нет в списке точки.")
    if status not in Attendance.Status.values:
        raise PayrollError("Выберите: вовремя, опоздал, не вышел или выходной.")
    if employee.id in full_from_day(shift):
        raise PayrollError("Сотрудник на полной смене — его уже отметил дневной администратор.")
    reason = reason.strip()
    rules = FineRules.current()
    attendance = Attendance.objects.filter(shift=shift, employee=employee).first() or Attendance(
        shift=shift, employee=employee,
    )
    was_absent = attendance.pk and attendance.status == Attendance.Status.ABSENT
    has_bonus = attendance.pk and (
        attendance.bonuses.exists() or attendance.penalties.exists() or attendance.extra_hours.exists()
    )

    if status == Attendance.Status.OFF:
        if has_bonus:
            raise PayrollError("У сотрудника есть бонус, штраф или часы за эту смену. Сначала удалите их.")
        arrived_at, minutes, reason, fine = None, 0, "", 0
    elif status == Attendance.Status.ON_TIME:
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
        if has_bonus:
            raise PayrollError("У сотрудника есть бонус, штраф или часы за эту смену. Сначала удалите их.")
        arrived_at, minutes, fine = None, 0, rules.absence_fine

    attendance.status = status
    worked = status in Attendance.WORKED
    attendance.kind = Attendance.Kind.FULL if (full and worked and can_be_full(shift)) else shift.kind
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


def parse_amount(value, what: str) -> int:
    try:
        amount = int(str(value).replace(" ", "").replace("\u00a0", ""))
    except ValueError:
        raise PayrollError(f"Сумма {what} — целое число в тенге, например 2000.")
    if not 0 < amount <= MAX_AMOUNT:
        raise PayrollError(f"Сумма {what} должна быть больше нуля.")
    return amount


def _require_worked(attendance: Attendance, what: str):
    if attendance.status not in Attendance.WORKED:
        raise PayrollError(f"Сотрудник не работает в эту смену — {what} поставить нельзя.")


def add_bonus(attendance: Attendance, *, amount: str | int, reason: str, user) -> Bonus:
    amount = parse_amount(amount, "бонуса")
    reason = reason.strip()
    if not reason:
        raise PayrollError("Напишите, за что бонус.")
    _require_worked(attendance, "бонус")
    bonus = Bonus.objects.create(attendance=attendance, amount=amount, reason=reason[:200], created_by=user)
    resync(attendance)
    return bonus


def remove_bonus(bonus: Bonus):
    attendance = bonus.attendance
    bonus.delete()
    resync(attendance)


def add_penalty(attendance: Attendance, *, amount: str | int, reason: str, user) -> Penalty:
    """Штраф во время смены: сотрудник что-то нарушил."""
    amount = parse_amount(amount, "штрафа")
    reason = reason.strip()
    if not reason:
        raise PayrollError("Напишите, за что штраф.")
    _require_worked(attendance, "штраф")
    penalty = Penalty.objects.create(attendance=attendance, amount=amount, reason=reason[:200], created_by=user)
    resync(attendance)
    return penalty


def remove_penalty(penalty: Penalty):
    attendance = penalty.attendance
    penalty.delete()
    resync(attendance)


def parse_hours(value) -> int:
    try:
        hours = int(str(value).strip())
    except ValueError:
        raise PayrollError("Выберите, сколько часов: 1, 2 или 3.")
    if not 0 < hours <= MAX_HOURS:
        raise PayrollError(f"Часов — от 1 до {MAX_HOURS}.")
    return hours


def add_hours(attendance: Attendance, *, kind: str, hours, reason: str = "", user) -> ExtraHours:
    """Переработка (задержался после смены) или недоработка (пришёл позже, ушёл раньше) в часах."""
    if kind not in ExtraHours.Kind.values:
        raise PayrollError("Выберите: переработка или недоработка.")
    hours = parse_hours(hours)
    _require_worked(attendance, "переработку" if kind == ExtraHours.Kind.OVER else "недоработку")
    entry = ExtraHours.objects.create(
        attendance=attendance, kind=kind, hours=hours, reason=reason.strip()[:200], created_by=user,
    )
    resync(attendance)
    return entry


def stayed_candidates(attendance: Attendance):
    """
    Кто мог задержаться за опоздавшего: те, кто работал на этом филиале, когда его смена началась —
    люди с предыдущей смены (дневной повар до 18:00, а ночной опоздал) или с этой же.
    """
    shift = attendance.shift
    return (
        Attendance.objects.filter(
            shift__point=shift.point, status__in=Attendance.WORKED,
            shift__planned_start__lte=shift.planned_start, shift__planned_end__gte=shift.planned_start,
        )
        .exclude(pk=attendance.pk)
        .select_related("employee", "shift")
        .order_by("shift__planned_start", "employee__full_name")
    )


@transaction.atomic
def cover_late(late: Attendance, helper: Attendance, *, hours, user) -> tuple[ExtraHours, ExtraHours]:
    """Сменщик опоздал, кто-то задержался за него: переработка задержавшемуся, недоработка опоздавшему."""
    hours = parse_hours(hours)
    _require_worked(late, "недоработку")
    if not stayed_candidates(late).filter(pk=helper.pk).exists():
        raise PayrollError("Этот сотрудник не работал на вашем филиале, когда началась смена опоздавшего.")
    under = ExtraHours.objects.create(
        attendance=late, kind=ExtraHours.Kind.UNDER, hours=hours, created_by=user,
        reason=f"за него задержался {helper.employee}"[:200],
    )
    over = ExtraHours.objects.create(
        attendance=helper, kind=ExtraHours.Kind.OVER, hours=hours, created_by=user, pair=under,
        reason=f"задержался за {late.employee}"[:200],
    )
    under.pair = over
    under.save(update_fields=["pair"])
    resync(late)
    resync(helper)
    return over, under


def _hours_pair(entry: ExtraHours) -> ExtraHours | None:
    return entry.pair or ExtraHours.objects.filter(pair=entry).first()


@transaction.atomic
def remove_hours(entry: ExtraHours):
    """Удалить часы. Если их ставили связкой «кто задержался», удаляется и вторая половина."""
    touched = [entry.attendance]
    pair = _hours_pair(entry)
    if pair is not None:
        touched.append(pair.attendance)
        pair.delete()
    entry.delete()
    for attendance in touched:
        resync(attendance)


def add_advance(employee: Employee, *, amount: str | int, comment: str = "", user, shift=None,
                point=None) -> Advance:
    """Аванс: деньги заранее. Вычитается из суммы к выплате в таблице."""
    amount = parse_amount(amount, "аванса")
    where = shift.point if shift is not None else (point or employee.point)
    advance = Advance.objects.create(
        employee=employee, amount=amount, comment=comment.strip()[:200], given_by=user, shift=shift, point=where,
        given_on=local(timezone.now(), where).date() if where else timezone.localdate(),
    )
    sheets.queue(advances=[advance.id])
    return advance


def remove_advance(advance: Advance):
    key = sheets.advance_key(advance)
    advance.delete()
    sheets.queue(deletes=[key])


def substitute_candidates(shift):
    """Кого можно вызвать на замену: резерв и сотрудники других филиалов, кто ещё не на этой смене."""
    already = Attendance.objects.filter(shift=shift).values("employee_id")
    return (
        Employee.objects.filter(is_active=True)
        .exclude(point=shift.point)
        .exclude(position=Employee.Position.ADMIN)
        .exclude(id__in=already)
        .select_related("point")
        .order_by("point__name", "full_name")
    )


def substitutes_on(shift):
    return (
        Attendance.objects.filter(shift=shift).exclude(employee__point=shift.point)
        .exclude(employee__position=Employee.Position.ADMIN)
        .select_related("employee__point", "substitute_for")
        .prefetch_related("bonuses", "penalties", "extra_hours")
    )


@transaction.atomic
def add_substitute(shift, employee: Employee, *, user, instead_of: Employee | None = None, position: str = "",
                   full: bool = False, now: datetime | None = None) -> Attendance:
    """Вызвали на замену человека с другого филиала или из резерва."""
    now = now or timezone.now()
    if employee.point_id == shift.point_id:
        raise PayrollError("Это сотрудник вашего филиала — отметьте его в общем списке.")
    if employee.position == Employee.Position.ADMIN or not employee.is_active:
        raise PayrollError("Этого человека нельзя вызвать на замену.")
    if Attendance.objects.filter(shift=shift, employee=employee).exists():
        raise PayrollError("Этот человек уже на смене.")
    if instead_of is not None and instead_of.point_id != shift.point_id:
        raise PayrollError("Заменять можно только сотрудника своего филиала.")
    if position and position not in Employee.Position.values:
        raise PayrollError("Выберите должность из списка.")
    position = position or (instead_of.position if instead_of else "")
    attendance = Attendance.objects.create(
        shift=shift, employee=employee, status=Attendance.Status.ON_TIME,
        kind=Attendance.Kind.FULL if (full and can_be_full(shift)) else shift.kind,
        arrived_at=now, position=position if position != employee.position else "",
        substitute_for=instead_of, marked_by=user,
    )
    sheets.queue([attendance.id])
    return attendance


def remove_substitute(attendance: Attendance):
    """Замену добавили по ошибке — убираем и из приложения, и из таблицы."""
    if not attendance.is_substitute:
        raise PayrollError("Это сотрудник вашего филиала: поставьте ему «Выходной».")
    key = sheets.shift_key(attendance)
    for entry in attendance.extra_hours.all():  # вторая половина связки у другого человека — тоже убрать
        pair = _hours_pair(entry)
        if pair is not None and pair.attendance_id != attendance.pk:
            other = pair.attendance
            pair.delete()
            resync(other)
    attendance.delete()
    sheets.queue(deletes=[key])


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
    attendance.kind = shift.kind
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
