from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from checklists.models import ChecklistTemplate
from checklists.services import open_tasks, progress
from payroll.services import marked_count, record_admin

from .forms import CheckinForm, LateReasonForm
from .models import Shift
from .services import current_shift_for, current_slot, perform_checkin, report_late


def client_ip(request) -> str | None:
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


@login_required
def home(request):
    user = request.user
    if not user.is_shift_admin:
        return manager_home(request)

    now = timezone.now()
    shift = current_shift_for(user, now)
    points = list(user.points.filter(is_active=True))
    # Пока у администратора одна точка, сразу показываем её смену.
    # Если точек несколько, смена определится по месту отметки.
    upcoming = current_slot(points[0], now) if len(points) == 1 else None

    return render(request, "shifts/home.html", {
        "shift": shift,
        "points": points,
        "upcoming": upcoming,
        "upcoming_name": dict(Shift.Kind.choices)[upcoming.kind] if upcoming else None,
        # Точки, где кнопка «Я пришёл» спрашивает геолокацию
        "location_point_ids": ",".join(str(p.id) for p in points if p.require_location),
        "late_form": LateReasonForm() if shift and shift.needs_late_reason else None,
        "tabs": acceptance_tabs(shift) if shift else [],
        "staff_marked": marked_count(shift) if shift else (0, 0),
        "tasks": list(open_tasks(shift.point)) if shift else [],
        "now": now,
    })


def acceptance_tabs(shift):
    """Вкладки приёма смены и сколько в каждой уже отмечено."""
    runs = {r.template_id: r for r in shift.checklist_runs.all()}
    tabs = []
    for template in ChecklistTemplate.objects.filter(is_active=True):
        run = runs.get(template.id)
        done, total = progress(run) if run else (0, 0)
        tabs.append({"template": template, "run": run, "done": done, "total": total})
    return tabs


def manager_home(request):
    user = request.user
    today_shifts = (
        Shift.objects.filter(point__in=user.points.all(), planned_end__gt=timezone.now() - timedelta(hours=24))
        .select_related("point", "admin")
        .order_by("-checked_in_at")
    )
    return render(request, "shifts/manager_home.html", {"shifts": today_shifts})


@login_required
@require_POST
def checkin(request):
    """Телефон присылает координаты и селфи. Ответ — JSON, его разбирает checkin.js."""
    if not request.user.is_shift_admin:
        return JsonResponse({"status": "forbidden", "message": "Отмечаться могут только администраторы смены."}, status=403)

    form = CheckinForm(request.POST, request.FILES)
    if not form.is_valid():
        first_error = next(iter(form.errors.values()))[0]
        return JsonResponse({"status": "invalid", "message": first_error}, status=400)

    data = form.cleaned_data
    result = perform_checkin(
        request.user, data["latitude"], data["longitude"], data["accuracy"], data["selfie"],
        point_id=data["point"], ip=client_ip(request),
    )
    return JsonResponse({
        "status": result.status,
        "message": result.message,
        "distance_m": round(result.distance_m) if result.distance_m is not None else None,
        "late_minutes": result.shift.late_minutes if result.shift else None,
    })


@login_required
@require_POST
def late_reason(request, shift_id: int):
    shift = get_object_or_404(Shift, pk=shift_id)
    if shift.admin_id != request.user.id:
        return HttpResponseForbidden()

    form = LateReasonForm(request.POST)
    if form.is_valid() and shift.needs_late_reason:
        shift.late_reason = form.cleaned_data["reason"]
        shift.save(update_fields=["late_reason"])
        record_admin(shift)  # причина опоздания — и в зарплатную таблицу
        report_late(shift)
        return redirect("home")

    return render(request, "shifts/home.html", {
        "shift": shift,
        "points": list(request.user.points.filter(is_active=True)),
        "upcoming": None,
        "late_form": form,
    })
