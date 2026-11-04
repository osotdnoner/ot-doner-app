from dataclasses import dataclass, field

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


def _back(shift, employee_id=None, request=None):
    if request is not None and request.POST.get("next") == "issues":
        return redirect(reverse("issues", args=[shift.id]))
    url = reverse("attendance", args=[shift.id])
    return redirect(f"{url}#emp-{employee_id}" if employee_id else url)


# Названия категорий на экране смены — должности во множественном числе
CATEGORY_TITLES = {
    "doner": "Донерщики", "pizza": "Пиццеристы", "cashier": "Кассиры", "runner_delivery": "Раннеры доставки",
    "runner": "Раннеры кассы", "cleaner": "Техперсонал", "cleaner_kitchen": "Техперсонал кухни",
    "cleaner_hall": "Техперсонал зала", "cook": "Повара горячего цеха", "operator": "Операторы", "other": "Другие",
}


@dataclass
class Person:
    employee: Employee
    a: Attendance | None = None         # отметка на этой смене
    from_day: Attendance | None = None  # ночная смена: человек на полной смене с дневной
    hint: str = ""                      # подпись под именем

    @property
    def is_on(self) -> bool:
        return self.from_day is not None or (self.a is not None and self.a.status in Attendance.WORKED)


@dataclass
class Category:
    code: str
    title: str
    people: list = field(default_factory=list)

    @property
    def on_count(self) -> int:
        return sum(p.is_on for p in self.people)


def _shift_records(shift) -> dict[int, Attendance]:
    return {
        a.employee_id: a
        for a in Attendance.objects.filter(shift=shift).exclude(status=Attendance.Status.OFF)
        .exclude(employee__position=Employee.Position.ADMIN)
        .select_related("employee__point", "substitute_for").prefetch_related("bonuses", "penalties", "extra_hours")
    }


@login_required
def attendance_page(request, shift_id: int):
    """
    Экран «Сотрудники на смене»: категории по должностям, в каждой — люди точки (свои и кто выходил сюда
    за месяц). Нажал на имя — пришёл. У отмеченных — бонус, штраф и «Ещё». Поиск — по всем точкам.
    Не вышел, опоздал, штраф с причиной — на странице «Косяки».
    """
    shift = _own_open_shift(request, shift_id)
    if shift.needs_late_reason:
        return redirect("home")
    records = _shift_records(shift)
    advances = {}
    for adv in Advance.objects.filter(shift=shift).order_by("id"):
        advances.setdefault(adv.employee_id, []).append(adv)
    from_day = full_from_day(shift)
    replaced = {a.substitute_for_id: a.employee for a in records.values() if a.substitute_for_id}

    people = {}
    for e in employees_for(shift):
        people[e.id] = Person(e)
    for employee_id, a in records.items():
        people.setdefault(employee_id, Person(a.employee)).a = a
        if a.status == Attendance.Status.LATE:  # кто мог задержаться за опоздавшего
            a.cover_candidates = list(stayed_candidates(a))
    for employee_id, a in from_day.items():
        people.setdefault(employee_id, Person(a.employee)).from_day = a

    categories = {code: Category(code, CATEGORY_TITLES.get(code, label))
                  for code, label in Employee.Position.choices if code != Employee.Position.ADMIN}
    for person in people.values():
        e = person.employee
        e.shift_advances = advances.get(e.id, [])
        e.replaced_by_sub = replaced.get(e.id)
        code = person.a.position_code if person.a else (person.from_day.position_code if person.from_day else e.position)
        categories.get(code, categories["other"]).people.append(person)
    for person in people.values():
        e, a, parts = person.employee, person.a, []
        if a and a.position:
            parts.append(f"сегодня {a.position_display.lower()}")
        if e.point_id != shift.point_id:
            parts.append(f"с {e.point}" if e.point_id else "резерв")
        if a and a.kind == Attendance.Kind.FULL:
            parts.append("полная смена")
        if a and a.substitute_for_id:
            parts.append(f"вместо {a.substitute_for}")
        if a and a.status == Attendance.Status.ABSENT:
            parts.append(a.reason)
        person.hint = " · ".join(parts)
    for c in categories.values():
        c.people.sort(key=lambda p: (p.employee.point_id != shift.point_id, p.employee.full_name.lower()))
    others = list(others_for(shift, exclude_ids=people.keys()))
    own_employees = [p.employee for p in people.values() if p.employee.point_id == shift.point_id]
    return render(request, "payroll/attendance.html", {
        "shift": shift,
        "categories": [c for c in categories.values() if c.people],
        "others": others,
        "count": on_shift_count(shift),
        "issues_count": _issues_count(records.values()),
        "own_employees": sorted(own_employees, key=lambda e: e.full_name.lower()),
        "start": local(shift.planned_start, shift.point),
        "sheet_on": bool(settings.GOOGLE_SHEET_URL),
        "can_be_full": can_be_full(shift),
        "full_end": shift.point.night_shift_end,
        "positions": [(v, label) for v, label in Employee.Position.choices if v != Employee.Position.ADMIN],
        "hour_choices": [1, 2, 3],
    })


def _issues_count(records) -> int:
    return sum((a.status in (Attendance.Status.ABSENT, Attendance.Status.LATE)) + a.penalties.count() for a in records)


@login_required
def issues_page(request, shift_id: int):
    """«Косяки»: кто не вышел, кто опоздал, штрафы — с причиной."""
    shift = _own_open_shift(request, shift_id)
    if shift.needs_late_reason:
        return redirect("home")
    records = _shift_records(shift)
    rules = FineRules.current()
    from_day = full_from_day(shift)
    expected = sorted(
        [e for e in employees_for(shift) if e.id not in from_day]
        + [a.employee for a in records.values() if a.employee.id not in {e.id for e in employees_for(shift)}],
        key=lambda e: e.full_name.lower(),
    )
    on_shift = sorted([a for a in records.values() if a.status in Attendance.WORKED],
                      key=lambda a: a.employee.full_name.lower())
    absent = [a for a in records.values() if a.status == Attendance.Status.ABSENT]
    late = [a for a in records.values() if a.status == Attendance.Status.LATE]
    penalties = [p for a in on_shift for p in a.penalties.all()]
    kind = request.GET.get("kind", "absent")
    return render(request, "payroll/issues.html", {
        "shift": shift,
        "kind": kind if kind in ("absent", "late", "penalty") else "absent",
        "expected": expected,
        "on_shift": on_shift,
        "absent": absent,
        "late": late,
        "penalties": penalties,
        "absence_fine": rules.absence_fine,
        "tiers": list(rules.tiers.all()),
        "now_hm": f"{local(timezone.now(), shift.point):%H:%M}",
        "start": local(shift.planned_start, shift.point),
    })


@login_required
@require_POST
def issue_add_view(request, shift_id: int):
    shift = _own_open_shift(request, shift_id)
    kind = request.POST.get("kind", "")
    back = f"{reverse('issues', args=[shift.id])}?kind={kind}"
    employee = Employee.objects.filter(pk=request.POST.get("employee") or 0).first()
    if employee is None:
        messages.error(request, "Выберите сотрудника.")
        return redirect(back)
    try:
        if kind == "absent":
            a = mark(shift, employee, Attendance.Status.ABSENT, user=request.user, reason=request.POST.get("reason", ""),
                     fine=request.POST.get("fine", ""))
            messages.warning(request, f"{employee}: не вышел{f', штраф {money(a.fine)}' if a.fine else ''}. "
                                      f"Управляющему ушло сообщение.")
        elif kind == "late":
            current = Attendance.objects.filter(shift=shift, employee=employee).first()
            a = mark(shift, employee, Attendance.Status.LATE, user=request.user,
                     arrived=request.POST.get("arrived", ""), reason=request.POST.get("reason", ""),
                     full=bool(current and current.kind == Attendance.Kind.FULL),
                     position=current.position if current else "")
            messages.warning(request, f"{employee}: опоздал на {a.late_human}"
                                      f"{f', штраф {money(a.fine)}' if a.fine else ''}.")
        elif kind == "penalty":
            a = get_object_or_404(Attendance, shift=shift, employee=employee, status__in=Attendance.WORKED)
            p = add_penalty(a, amount=request.POST.get("amount", ""), reason=request.POST.get("reason", ""),
                            user=request.user)
            messages.warning(request, f"{employee}: штраф {money(p.amount)} — {p.reason}.")
        else:
            messages.error(request, "Выберите: не вышел, опоздал или штраф.")
    except PayrollError as error:
        messages.error(request, f"{employee}: {error}")
    return redirect(back)


@login_required
@require_POST
def late_clear_view(request, shift_id: int, attendance_id: int):
    """Опоздание записали по ошибке — человек пришёл вовремя."""
    shift = _own_open_shift(request, shift_id)
    a = get_object_or_404(Attendance.objects.select_related("employee"), pk=attendance_id, shift=shift)
    mark(shift, a.employee, Attendance.Status.ON_TIME, user=request.user, full=a.kind == Attendance.Kind.FULL,
         position=a.position)
    messages.success(request, f"{a.employee}: опоздание убрано.")
    return redirect(f"{reverse('issues', args=[shift.id])}?kind=late")


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
    return _back(shift, employee_id, request)


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
        return _back(shift, attendance.employee_id, request)
    messages.success(request, f"{attendance.employee} убран со смены.")
    return _back(shift, None, request)


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
    employee = get_object_or_404(Employee, pk=request.POST.get("employee") or employee_id, is_active=True)
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
