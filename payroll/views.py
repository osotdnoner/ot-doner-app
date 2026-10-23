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

from .models import Advance, Attendance, Bonus, FineRules, Penalty
from .services import (
    PayrollError, add_advance, add_bonus, add_penalty, add_substitute, can_be_full, employees_for, full_from_day,
    local, mark, marked_count, remove_advance, remove_bonus, remove_penalty, remove_substitute,
    substitute_candidates, substitutes_on,
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
    shift = _own_open_shift(request, shift_id)
    if shift.needs_late_reason:
        return redirect("home")
    records = {
        a.employee_id: a
        for a in Attendance.objects.filter(shift=shift).prefetch_related("bonuses", "penalties")
    }
    advances = {}
    for adv in Advance.objects.filter(shift=shift).order_by("id"):
        advances.setdefault(adv.employee_id, []).append(adv)
    for attendance in records.values():
        attendance.shift_advances = advances.get(attendance.employee_id, [])
    from_day = full_from_day(shift)
    employees = list(employees_for(shift))
    for e in employees:
        e.shift_advances = advances.get(e.id, [])
    rows = [(e, records.get(e.id), from_day.get(e.id)) for e in employees]
    subs = list(substitutes_on(shift))
    replaced = {a.substitute_for_id: a.employee for a in subs if a.substitute_for_id}
    for a in subs:
        a.employee.shift_advances = advances.get(a.employee_id, [])
    for e in employees:
        e.replaced_by_sub = replaced.get(e.id)
    done, total = marked_count(shift)
    rules = FineRules.current()
    return render(request, "payroll/attendance.html", {
        "shift": shift,
        "rows": rows,
        "done": done,
        "total": total,
        "start": local(shift.planned_start, shift.point),
        "now_hm": f"{local(timezone.now(), shift.point):%H:%M}",
        "tiers": list(rules.tiers.all()),
        "absence_fine": rules.absence_fine,
        "sheet_on": bool(settings.GOOGLE_SHEET_URL),
        "can_be_full": can_be_full(shift),
        "full_end": shift.point.night_shift_end,
        "subs": subs,
        "candidates": substitute_candidates(shift),
        "own_employees": employees,
        "positions": [(v, label) for v, label in Employee.Position.choices if v != Employee.Position.ADMIN],
    })


@login_required
@require_POST
def mark_view(request, shift_id: int, employee_id: int):
    shift = _own_open_shift(request, shift_id)
    employee = get_object_or_404(shift.point.employees, pk=employee_id)
    try:
        attendance = mark(
            shift, employee, request.POST.get("status", ""), user=request.user,
            arrived=request.POST.get("arrived", ""), reason=request.POST.get("reason", ""),
            full=request.POST.get("full") == "1",
        )
    except PayrollError as error:
        messages.error(request, f"{employee}: {error}")
        return _back(shift, employee.id)
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
def substitute_view(request, shift_id: int):
    shift = _own_open_shift(request, shift_id)
    employee = Employee.objects.filter(pk=request.POST.get("employee") or 0).first()
    if employee is None:
        messages.error(request, "Выберите, кто вышел на замену.")
        return _back(shift)
    instead_of = shift.point.employees.filter(pk=request.POST.get("instead_of") or 0).first()
    try:
        add_substitute(shift, employee, user=request.user, instead_of=instead_of,
                       position=request.POST.get("position", ""), full=request.POST.get("full") == "1")
    except PayrollError as error:
        messages.error(request, f"{employee}: {error}")
        return _back(shift)
    messages.success(request, f"{employee} на замене.")
    return _back(shift, employee.id)


@login_required
@require_POST
def substitute_remove_view(request, shift_id: int, attendance_id: int):
    shift = _own_open_shift(request, shift_id)
    attendance = get_object_or_404(Attendance.objects.select_related("employee", "shift"), pk=attendance_id, shift=shift)
    try:
        remove_substitute(attendance)
    except PayrollError as error:
        messages.error(request, str(error))
    return _back(shift)
