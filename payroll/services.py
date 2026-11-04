"""
Правила зарплатного учёта смены: кто вышел, опоздания, штрафы, бонусы.

Администратор после отметки «Я пришёл» отмечает сотрудников: пришёл вовремя, опоздал
(во сколько пришёл и почему), не вышел (почему) или выходной (не его смена).
Дневной администратор может отметить полную смену — до конца ночной; ночной администратор
видит таких сотрудников уже отмеченными. Штраф считается сам по ступеням из панели.
Бонус, штраф во время смены и аванс администратор ставит сам: сумма и за что.
Переработку и недоработку — в часах. Если сменщик опоздал и кто-то задержался за него, одно
нажатие ставит переработку задержавшемуся и недоработку опоздавшему. Деньги за часы считает таблица.
Сотрудники — общая база сети. В списке точки — её сотрудники и те, кто выходил сюда за последний месяц;
любого другого админ находит поиском по имени. Отмечать нужно только тех, кто пришёл (или не вышел).
Нового человека админ добавляет прямо со смены, ушедшего — кнопкой «Больше не работает»; шефу приходит сообщение.
Каждое изменение сразу уходит в Google Таблицу (см. sheets.py).
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from notifications.models import Notification
from notifications.service import notify
from staff.models import Employee

from . import sheets
from .models import Advance, Attendance, Bonus, ExtraHours, FineRules, Penalty

MAX_AMOUNT = 1_000_000
MAX_HOURS = 12
RECENT_DAYS = 30  # кто выходил на точку за это время — в её списке
STALE_DAYS = 45   # свой сотрудник, не выходивший столько дней, из списка точки скрывается (поиск находит)


class PayrollError(Exception):
    """Понятная администратору ошибка: что исправить."""


def local(dt: datetime, point) -> datetime:
    return timezone.localtime(dt, ZoneInfo(point.timezone))


def staff_base():
    """Все, кого можно отметить: работающие сотрудники сети, кроме администраторов."""
    return Employee.objects.filter(is_active=True).exclude(position=Employee.Position.ADMIN).select_related("point")


def regulars_for(shift):
    """
    Список точки на экране смены: её сотрудники и те, кто выходил сюда за последние 30 дней.
    Свой сотрудник, который не выходил 45 дней (и добавлен давно), из списка скрывается — поиск его найдёт.
    """
    start = shift.planned_start
    worked_here = Attendance.objects.filter(
        shift__point=shift.point, status__in=Attendance.WORKED,
        shift__planned_start__gte=start - timedelta(days=RECENT_DAYS),
    ).values("employee_id")
    worked_anywhere = Attendance.objects.filter(
        status__in=Attendance.WORKED, shift__planned_start__gte=start - timedelta(days=STALE_DAYS),
    ).values("employee_id")
    fresh = Q(created_at__gte=start - timedelta(days=STALE_DAYS))
    own = Q(point=shift.point) & (Q(id__in=worked_anywhere) | fresh)
    return staff_base().filter(own | Q(id__in=worked_here))


def employees_for(shift):
    """Список точки по порядку: сначала свои по должностям, потом с других точек."""
    people = list(regulars_for(shift))
    return sorted(people, key=lambda e: (e.point_id != shift.point_id, e.position, e.full_name.lower()))


def others_for(shift, exclude_ids=()):
    """Остальные сотрудники сети — для поиска по имени."""
    return (
        staff_base().exclude(id__in=list(exclude_ids))
        .order_by("point__name", "full_name")
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


def on_shift_count(shift) -> int:
    """Сколько человек отмечено на смене (пришли, опоздали или не вышли), без администратора."""
    ids = set(
        Attendance.objects.filter(shift=shift).exclude(status=Attendance.Status.OFF)
        .exclude(employee__position=Employee.Position.ADMIN).values_list("employee_id", flat=True)
    )
    return len(ids | set(full_from_day(shift)))


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
         full: bool = False, position: str = "", instead_of: Employee | None = None, fine=None,
         now: datetime | None = None) -> Attendance:
    """
    Отметка сотрудника. position — кем он работает именно в эту смену, если не по своей обычной должности
    (например, кассир сегодня на раннере кассы). Пусто — обычная должность.
    """
    now = now or timezone.now()
    if employee.position == Employee.Position.ADMIN:
        raise PayrollError("Этого сотрудника нет в списке: администратор отмечается кнопкой «Я пришёл».")
    if not employee.is_active:
        raise PayrollError("Сотрудник отмечен как «Больше не работает». Вернуть его может управляющий в панели.")
    if status not in Attendance.Status.values:
        raise PayrollError("Выберите: вовремя, опоздал, не вышел или выходной.")
    if employee.id in full_from_day(shift):
        raise PayrollError("Сотрудник на полной смене — его уже отметил дневной администратор.")
    if position and (position not in Employee.Position.values or position == Employee.Position.ADMIN):
        raise PayrollError("Выберите должность из списка.")
    if instead_of is not None and instead_of.id == employee.id:
        instead_of = None
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
        arrived_at, minutes = None, 0
        # Штраф за невыход: сколько вписал админ (0 — без штрафа), а если пусто — по правилам шефа
        typed = "" if fine is None else str(fine).strip()
        if not typed:
            fine = rules.absence_fine
        elif typed.strip("0") == "":
            fine = 0
        else:
            fine = parse_amount(typed, "штрафа")

    attendance.status = status
    worked = status in Attendance.WORKED
    attendance.kind = Attendance.Kind.FULL if (full and worked and can_be_full(shift)) else shift.kind
    attendance.position = position if (worked and position != employee.position) else ""
    if instead_of is not None or not worked:
        attendance.substitute_for = instead_of if worked else None
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


def remove_from_shift(attendance: Attendance):
    """Отметили по ошибке — человек не на этой смене. Строка уходит и из таблицы."""
    if attendance.bonuses.exists() or attendance.penalties.exists() or attendance.extra_hours.exists():
        raise PayrollError("У сотрудника есть бонус, штраф или часы за эту смену. Сначала удалите их.")
    key = sheets.shift_key(attendance)
    attendance.delete()
    sheets.queue(deletes=[key])


def _boss_message(shift, title: str, text: str):
    notify(shift.point, Notification.Event.STAFF, title, text)


@transaction.atomic
def add_new_employee(shift, *, full_name: str, position: str, user, phone: str = "", kaspi_phone: str = "",
                     kaspi_name: str = "", full: bool = False, now: datetime | None = None) -> Attendance:
    """Новый человек пришёл на первую смену: админ добавляет его в базу и сразу отмечает «Вовремя»."""
    full_name = " ".join(full_name.split())
    if len(full_name) < 2:
        raise PayrollError("Напишите имя и фамилию нового сотрудника.")
    if position not in Employee.Position.values or position == Employee.Position.ADMIN:
        raise PayrollError("Выберите должность.")
    # Сравниваем в Python: регистр кириллицы в базе SQLite не учитывается
    twin = next((e for e in Employee.objects.select_related("point").only("full_name", "is_active", "point__name")
                 if e.full_name.lower() == full_name.lower()), None)
    if twin is not None:
        where = "уже есть в базе" if twin.is_active else "есть в базе, но отмечен «Больше не работает»"
        raise PayrollError(f"«{twin.full_name}» ({twin.home}) {where}. Найдите его поиском — или допишите фамилию, "
                           f"если это другой человек.")
    employee = Employee(
        point=shift.point, full_name=full_name[:120], position=position, phone=phone.strip()[:20],
        kaspi_phone=kaspi_phone, kaspi_name=kaspi_name, added_by=user,
    )
    try:
        employee.full_clean(exclude=["user"])
    except ValidationError as error:
        raise PayrollError(" ".join(m for msgs in error.message_dict.values() for m in msgs))
    employee.save()
    attendance = mark(shift, employee, Attendance.Status.ON_TIME, user=user, full=full, now=now)
    kaspi = f"Kaspi: {employee.kaspi_phone} · {employee.kaspi_name}" if employee.kaspi_phone else "Kaspi не указан"
    _boss_message(
        shift, "Новый сотрудник",
        f"Точка «{shift.point}»: администратор {user} добавил {employee} "
        f"({employee.get_position_display().lower()}) и отметил на смене.\n{kaspi}. "
        f"Проверьте карточку в панели «Сотрудники».",
    )
    return attendance


def mark_left(shift, employee: Employee, *, user, today=None) -> Employee:
    """«Больше не работает»: человек пропадает из списков, история и зарплата остаются."""
    if employee.point_id != shift.point_id:
        raise PayrollError("Это сотрудник другой точки — отметить уход может его администратор или управляющий.")
    if employee.position == Employee.Position.ADMIN:
        raise PayrollError("Администраторов отключает управляющий в панели.")
    employee.is_active = False
    employee.left_at = today or local(timezone.now(), shift.point).date()
    employee.left_by = user
    employee.save(update_fields=["is_active", "left_at", "left_by"])
    _boss_message(
        shift, "Сотрудник больше не работает",
        f"Точка «{shift.point}»: администратор {user} отметил, что {employee} "
        f"({employee.get_position_display().lower()}) больше не работает. "
        f"Если это ошибка — верните его в панели «Сотрудники» (галочка «Работает»).",
    )
    return employee


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
