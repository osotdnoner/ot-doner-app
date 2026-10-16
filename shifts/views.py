from datetime import timedelta
from zoneinfo import ZoneInfo

from django.contrib.auth.decorators import login_required
from django.http import HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from notifications.telegram import notify_point_managers

from .forms import CheckinForm, LateReasonForm
from .models import Shift
from .services import current_shift_for, current_slot, perform_checkin


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
        "late_form": LateReasonForm() if shift and shift.needs_late_reason else None,
    })


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
        ip=client_ip(request),
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
        local_time = timezone.localtime(shift.checked_in_at, ZoneInfo(shift.point.timezone))
        notify_point_managers(
            shift.point,
            f"Точка «{shift.point}», {shift.get_kind_display().lower()} смена.\n"
            f"Администратор {request.user} отметился в {local_time:%H:%M}, "
            f"опоздание {shift.late_human}.\n"
            f"Причина: {shift.late_reason}",
        )
        return redirect("home")

    return render(request, "shifts/home.html", {
        "shift": shift,
        "points": list(request.user.points.filter(is_active=True)),
        "upcoming": None,
        "late_form": form,
    })
