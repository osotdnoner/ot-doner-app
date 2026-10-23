from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from shifts.models import Shift
from staff.models import Employee

from .models import ChecklistTemplate, Item, Run, StaffCheck, Status, Task
from .services import (
    DEADLINE_CHOICES, ChecklistError, answer_item, answer_staff, build_view, close_task, finish_run,
    get_or_start_run, photo_required, progress, staff_on_point,
)

MAX_PHOTO_BYTES = 6 * 1024 * 1024


def _own_open_shift(request, shift_id) -> Shift:
    shift = get_object_or_404(Shift.objects.select_related("point"), pk=shift_id, admin=request.user)
    if shift.status != Shift.Status.OPEN or shift.planned_end <= timezone.now():
        raise Http404("Смена уже закончилась")
    return shift


def _own_run(request, run_id) -> Run:
    run = get_object_or_404(Run.objects.select_related("shift__point", "template"), pk=run_id)
    if run.shift.admin_id != request.user.id:
        raise Http404
    return run


def _photo(request):
    photo = request.FILES.get("photo")
    if photo and photo.size > MAX_PHOTO_BYTES:
        raise ChecklistError("Фото слишком большое.")
    return photo


def _error(message: str, status: int = 400):
    return JsonResponse({"ok": False, "message": message}, status=status)


@login_required
def run_page(request, shift_id: int, code: str):
    shift = _own_open_shift(request, shift_id)
    if shift.needs_late_reason:
        return redirect("home")
    template = get_object_or_404(ChecklistTemplate, code=code, is_active=True)
    run = get_or_start_run(shift, template)
    sections = build_view(run)
    for view in sections:
        for entry in view.entries:
            entry.photo_ok = photo_required(run, entry.item, entry.toilet_no, Status.OK)
            entry.photo_problem = photo_required(run, entry.item, entry.toilet_no, Status.DEVIATION)
    done, total = progress(run)
    return render(request, "checklists/run.html", {
        "shift": shift,
        "run": run,
        "sections": sections,
        "done": done,
        "total": total,
        "employees": staff_on_point(shift.point),
        "deadlines": DEADLINE_CHOICES,
        "uniform_items": StaffCheck.UNIFORM_ITEMS,
    })


@login_required
@require_POST
def answer_item_view(request, run_id: int, item_id: int, toilet_no: int):
    run = _own_run(request, run_id)
    item = get_object_or_404(Item.objects.select_related("section"), pk=item_id)
    responsible = None
    if request.POST.get("responsible"):
        responsible = Employee.objects.filter(pk=request.POST["responsible"]).first()
    try:
        deadline = int(request.POST.get("deadline") or 0)
        answer_item(
            run, item, toilet_no,
            status=request.POST.get("status", ""),
            problem=request.POST.get("problem", ""),
            comment=request.POST.get("comment", ""),
            photo=_photo(request),
            responsible=responsible,
            deadline_minutes=deadline,
            user=request.user,
        )
    except (ChecklistError, ValueError) as error:
        return _error(str(error) if isinstance(error, ChecklistError) else "Неверный срок.")
    done, total = progress(run)
    return JsonResponse({"ok": True, "done": done, "total": total})


@login_required
@require_POST
def answer_staff_view(request, run_id: int, employee_id: int):
    run = _own_run(request, run_id)
    employee = get_object_or_404(Employee, pk=employee_id)
    uniform = request.POST.get("uniform")
    try:
        answer_staff(
            run, employee,
            uniform_ok={"ok": True, "violation": False}.get(uniform),
            missing=request.POST.getlist("missing"),
            not_admitted=request.POST.get("not_admitted") == "1",
            comment=request.POST.get("comment", ""),
            photo=_photo(request),
            user=request.user,
        )
    except ChecklistError as error:
        return _error(str(error))
    done, total = progress(run)
    return JsonResponse({"ok": True, "done": done, "total": total})


@login_required
@require_POST
def finish_view(request, run_id: int):
    run = _own_run(request, run_id)
    try:
        finish_run(run)
    except ChecklistError as error:
        messages.error(request, str(error))
        return redirect("checklist", shift_id=run.shift_id, code=run.template.code)
    messages.success(request, f"«{run.template.title}» завершён.")
    return redirect("home")


@login_required
@require_POST
def close_task_view(request, task_id: int):
    task = get_object_or_404(Task, pk=task_id)
    if not request.user.is_shift_admin or not request.user.points.filter(pk=task.point_id).exists():
        raise Http404
    try:
        close_task(task, request.user, _photo(request))
    except ChecklistError as error:
        return _error(str(error))
    return JsonResponse({"ok": True})
