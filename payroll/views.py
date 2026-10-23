from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from shifts.models import Shift

from .models import Attendance, Bonus, FineRules
from .templatetags.money import money
from .services import PayrollError, add_bonus, employees_for, local, mark, marked_count, remove_bonus


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
        for a in Attendance.objects.filter(shift=shift).prefetch_related("bonuses")
    }
    rows = [(e, records.get(e.id)) for e in employees_for(shift)]
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
