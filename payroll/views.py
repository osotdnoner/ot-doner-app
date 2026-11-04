from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from shifts.models import Shift
from staff.models import Employee

from .models import Advance, Attendance, Bonus, ExtraHours, FineRules, Penalty
from .services import (
    PayrollError, add_advance, add_bonus, add_hours, add_new_employee, add_penalty, can_be_full, cover_late,
    employees_for, full_from_day, local, mark, mark_left, on_shift_count, others_for, remove_advance, remove_bonus,
    remove_from_shift, remove_hours, remove_penalty, stayed_candidates,
)
from .templatetags.money import money


def _own_open_shift(request, shift_id) -> Shift:
    shift = get_object_or_404(Shift.objects.select_related("point"), pk=shift_id, admin=request.user)
    if shift.status != Shift.Status.OPEN or shift.planned_end <= timezone.now():
        raise Http404("Смена уже закончилась")
    return shift


def _back(shift, employee_id=None):
    url = reverse("attendance", args=[shift.id])
    return redirect(f"{url}#emp-{employee_id}" if employee_id else url)


@login_required
def attendance_page(request, shift_id: int):
    """
    Экран «Сотрудники на смене»: кто уже на смене, список точки (свои и кто выходил сюда за месяц)
    и поиск по всем сотрудникам сети. Отмечать нужно только тех, кто пришёл или не вышел.
    """
    shift = _own_open_shift(request, shift_id)
    if shift.needs_late_reason:
        return redirect("home")
    records = {
        a.employee_id: a
        for a in Attendance.objects.filter(shift=shift).exclude(status=Attendance.Status.OFF)
        .exclude(employee__position=Employee.Position.ADMIN)
        .select_related("employee__point", "substitute_for").prefetch_related("bonuses", "penalties", "extra_hours")
    }
    advances = {}
    for adv in Advance.objects.filter(shift=shift).order_by("id"):
        advances.setdefault(adv.employee_id, []).append(adv)
    for a in records.values():
        a.employee.shift_advances = advances.get(a.employee_id, [])
        if a.status == Attendance.Status.LATE:  # кто мог задержаться за опоздавшего
            a.cover_candidates = list(stayed_candidates(a))
    from_day = full_from_day(shift)
    replaced = {a.substitute_for_id: a.employee for a in records.values() if a.substitute_for_id}

    on_shift = sorted(records.values(), key=lambda a: (a.status == Attendance.Status.ABSENT, a.employee.full_name.lower()))
    for a in on_shift:
        a.employee.replaced_by_sub = replaced.get(a.employee_id)
    regulars = [e for e in employees_for(shift) if e.id not in records and e.id not in from_day]
    for e in regulars:
        e.shift_advances = advances.get(e.id, [])
        e.replaced_by_sub = replaced.get(e.id)
    taken = set(records) | set(from_day) | {e.id for e in regulars}
    others = list(others_for(shift, exclude_ids=taken))
    rules = FineRules.current()
    return render(request, "payroll/attendance.html", {
        "shift": shift,
        "on_shift": on_shift,
        "from_day": list(from_day.values()),
        "regulars": regulars,
        "others": others,
        "count": on_shift_count(shift),
        "own_employees": [e for e in regulars if e.point_id == shift.point_id]
                         + [a.employee for a in on_shift if a.employee.point_id == shift.point_id],
        "start": local(shift.planned_start, shift.point),
        "now_hm": f"{local(timezone.now(), shift.point):%H:%M}",
        "tiers": list(rules.tiers.all()),
        "absence_fine": rules.absence_fine,
        "sheet_on": bool(settings.GOOGLE_SHEET_URL),
        "can_be_full": can_be_full(shift),
        "full_end": shift.point.night_shift_end,
        "positions": [(v, label) for v, label in Employee.Position.choices if v != Employee.Position.ADMIN],
        "hour_choices": [1, 2, 3],
    })


@login_required
@require_POST
def mark_view(request, shift_id: int, employee_id: int):
    shift = _own_open_shift(request, shift_id)
    employee = get_object_or_404(Employee, pk=employee_id)
    instead_of = shift.point.employees.filter(pk=request.POST.get("instead_of") or 0).first()
    try:
        attendance = mark(
            shift, employee, request.POST.get("status", ""), user=request.user,
            arrived=request.POST.get("arrived", ""), reason=request.POST.get("reason", ""),
            full=request.POST.get("full") == "1", position=request.POST.get("position", ""), instead_of=instead_of,
        )
    except PayrollError as error:
        messages.error(request, f"{employee}: {error}")
        return _back(shift, employee.id)
    if attendance.position:
        messages.info(request, f"{employee}: сегодня {attendance.position_display.lower()}.")
    if attendance.fine:
        messages.warning(request, f"{employee}: штраф {money(attendance.fine)}.")
    return _back(shift, employee.id)


@login_required
@require_POST
def bonus_view(request, shift_id: int, employee_id: int):
    shift = _own_open_shift(request, shift_id)
    attendance = get_object_or_404(Attendance, shift=shift, employee_id=employee_id)
    try:
        bonus = add_bonus(attendance, amount=request.POST.get("amount", ""), reason=request.POST.get("reason", ""),
                          user=request.user)
    except PayrollError as error:
        messages.error(request, f"{attendance.employee}: {error}")
        return _back(shift, employee_id)
    messages.success(request, f"{attendance.employee}: бонус {money(bonus.amount)}.")
    return _back(shift, employee_id)


@login_required
@require_POST
def bonus_delete_view(request, shift_id: int, bonus_id: int):
    shift = _own_open_shift(request, shift_id)
    bonus = get_object_or_404(Bonus.objects.select_related("attendance"), pk=bonus_id, attendance__shift=shift)
    employee_id = bonus.attendance.employee_id
    remove_bonus(bonus)
    return _back(shift, employee_id)


def _attendance_or_404(shift, employee_id):
    return get_object_or_404(Attendance.objects.select_related("employee"), shift=shift, employee_id=employee_id)


@login_required
@require_POST
def penalty_view(request, shift_id: int, employee_id: int):
    shift = _own_open_shift(request, shift_id)
    attendance = _attendance_or_404(shift, employee_id)
    try:
        penalty = add_penalty(attendance, amount=request.POST.get("amount", ""), reason=request.POST.get("reason", ""),
                              user=request.user)
    except PayrollError as error:
        messages.error(request, f"{attendance.employee}: {error}")
        return _back(shift, employee_id)
    messages.warning(request, f"{attendance.employee}: штраф {money(penalty.amount)} — {penalty.reason}.")
    return _back(shift, employee_id)


@login_required
@require_POST
def penalty_delete_view(request, shift_id: int, penalty_id: int):
    shift = _own_open_shift(request, shift_id)
    penalty = get_object_or_404(Penalty.objects.select_related("attendance"), pk=penalty_id, attendance__shift=shift)
    employee_id = penalty.attendance.employee_id
    remove_penalty(penalty)
    return _back(shift, employee_id)


@login_required
@require_POST
def advance_view(request, shift_id: int, employee_id: int):
    shift = _own_open_shift(request, shift_id)
    # Аванс — сотруднику своего филиала или тому, кто сейчас на замене
    employee = get_object_or_404(
        Employee.objects.filter(Q(point=shift.point) | Q(attendance__shift=shift)).distinct(), pk=employee_id,
    )
    try:
        advance = add_advance(employee, amount=request.POST.get("amount", ""), comment=request.POST.get("comment", ""),
                              user=request.user, shift=shift)
    except PayrollError as error:
        messages.error(request, f"{employee}: {error}")
        return _back(shift, employee_id)
    messages.success(request, f"{employee}: аванс {money(advance.amount)} записан.")
    return _back(shift, employee_id)


@login_required
@require_POST
def advance_delete_view(request, shift_id: int, advance_id: int):
    shift = _own_open_shift(request, shift_id)
    advance = get_object_or_404(Advance, pk=advance_id, shift=shift)
    employee_id = advance.employee_id
    remove_advance(advance)
    return _back(shift, employee_id)


@login_required
@require_POST
def remove_view(request, shift_id: int, attendance_id: int):
    """Отметили по ошибке — убрать человека со смены."""
    shift = _own_open_shift(request, shift_id)
    attendance = get_object_or_404(Attendance.objects.select_related("employee"), pk=attendance_id, shift=shift)
    try:
        remove_from_shift(attendance)
    except PayrollError as error:
        messages.error(request, f"{attendance.employee}: {error}")
        return _back(shift, attendance.employee_id)
    messages.success(request, f"{attendance.employee} убран со смены.")
    return _back(shift)


@login_required
@require_POST
def new_employee_view(request, shift_id: int):
    shift = _own_open_shift(request, shift_id)
    post = request.POST
    try:
        attendance = add_new_employee(
            shift, full_name=post.get("full_name", ""), position=post.get("position", ""), user=request.user,
            phone=post.get("phone", ""), kaspi_phone=post.get("kaspi_phone", ""), kaspi_name=post.get("kaspi_name", ""),
            full=post.get("full") == "1",
        )
    except PayrollError as error:
        messages.error(request, f"Новый сотрудник: {error}")
        return redirect(f"{reverse('attendance', args=[shift.id])}#new-employee")
    messages.success(request, f"{attendance.employee} добавлен и отмечен на смене. Управляющему ушло сообщение.")
    return _back(shift, attendance.employee_id)


@login_required
@require_POST
def left_view(request, shift_id: int, employee_id: int):
    """«Больше не работает»."""
    shift = _own_open_shift(request, shift_id)
    employee = get_object_or_404(Employee, pk=employee_id, is_active=True)
    try:
        mark_left(shift, employee, user=request.user)
    except PayrollError as error:
        messages.error(request, f"{employee}: {error}")
        return _back(shift, employee_id)
    messages.success(request, f"{employee} больше не в списке. Управляющему ушло сообщение.")
    return _back(shift)


def _hours_from(post) -> str:
    """Часы: кнопка 1/2/3 или своё число в поле «другое»."""
    return (post.get("hours_other") or "").strip() or post.get("hours", "")


@login_required
@require_POST
def hours_view(request, shift_id: int, employee_id: int):
    shift = _own_open_shift(request, shift_id)
    attendance = _attendance_or_404(shift, employee_id)
    try:
        entry = add_hours(attendance, kind=request.POST.get("kind", ""), hours=_hours_from(request.POST),
                          reason=request.POST.get("reason", ""), user=request.user)
    except PayrollError as error:
        messages.error(request, f"{attendance.employee}: {error}")
        return _back(shift, employee_id)
    messages.success(request, f"{attendance.employee}: {entry.get_kind_display().lower()} {entry.hours} ч.")
    return _back(shift, employee_id)


@login_required
@require_POST
def cover_view(request, shift_id: int, employee_id: int):
    """Опоздавший: кто задержался за него и на сколько часов."""
    shift = _own_open_shift(request, shift_id)
    late = _attendance_or_404(shift, employee_id)
    helper = Attendance.objects.select_related("employee").filter(pk=request.POST.get("helper") or 0).first()
    if helper is None:
        messages.error(request, f"{late.employee}: выберите, кто задержался.")
        return _back(shift, employee_id)
    try:
        over, _ = cover_late(late, helper, hours=_hours_from(request.POST), user=request.user)
    except PayrollError as error:
        messages.error(request, f"{late.employee}: {error}")
        return _back(shift, employee_id)
    messages.success(request, f"{helper.employee}: переработка {over.hours} ч, {late.employee}: недоработка {over.hours} ч.")
    return _back(shift, employee_id)


@login_required
@require_POST
def hours_delete_view(request, shift_id: int, entry_id: int):
    shift = _own_open_shift(request, shift_id)
    entry = get_object_or_404(
        ExtraHours.objects.select_related("attendance"),
        Q(attendance__shift=shift) | Q(pair__attendance__shift=shift), pk=entry_id,
    )
    employee_id = entry.attendance.employee_id if entry.attendance.shift_id == shift.id else entry.pair.attendance.employee_id
    remove_hours(entry)
    return _back(shift, employee_id)
