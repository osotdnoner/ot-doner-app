from zoneinfo import ZoneInfo

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from .models import CheckinAttempt, Shift


def local(dt, point):
    return timezone.localtime(dt, ZoneInfo(point.timezone)) if dt and point else dt


@admin.register(Shift)
class ShiftAdmin(admin.ModelAdmin):
    list_display = ("point", "kind", "admin", "planned_start_local", "checked_in_local", "late_badge", "distance", "selfie_thumb", "status")
    list_filter = ("point", "kind", "status")
    search_fields = ("admin__username", "admin__first_name", "admin__last_name")
    date_hierarchy = "planned_start"
    readonly_fields = (
        "point", "admin", "kind", "planned_start", "planned_end", "checked_in_at", "latitude", "longitude",
        "accuracy_m", "distance_m", "late_minutes", "late_reason", "selfie_preview", "map_link",
    )
    fields = readonly_fields + ("status",)

    def has_add_permission(self, request):
        return False  # смена создаётся только отметкой с телефона

    @admin.display(description="Начало по графику", ordering="planned_start")
    def planned_start_local(self, obj):
        return f"{local(obj.planned_start, obj.point):%d.%m %H:%M}"

    @admin.display(description="Отметка", ordering="checked_in_at")
    def checked_in_local(self, obj):
        return f"{local(obj.checked_in_at, obj.point):%H:%M}"

    @admin.display(description="Опоздание", ordering="late_minutes")
    def late_badge(self, obj):
        if not obj.late_minutes:
            return format_html('<span style="color:#1a7f37">вовремя</span>')
        return format_html('<b style="color:#b42318">{}</b>', obj.late_human)

    @admin.display(description="До точки")
    def distance(self, obj):
        return f"{obj.distance_m:.0f} м"

    @admin.display(description="Селфи")
    def selfie_thumb(self, obj):
        if not obj.selfie:
            return "—"
        return format_html('<img src="{}" style="height:48px;border-radius:6px">', obj.selfie.url)

    @admin.display(description="Селфи")
    def selfie_preview(self, obj):
        if not obj.selfie:
            return "—"
        return format_html('<img src="{}" style="max-height:320px;border-radius:8px">', obj.selfie.url)

    @admin.display(description="Где отметился")
    def map_link(self, obj):
        return format_html(
            '<a href="https://www.google.com/maps?q={},{}" target="_blank" rel="noopener">Открыть на карте</a>',
            obj.latitude, obj.longitude,
        )


@admin.register(CheckinAttempt)
class CheckinAttemptAdmin(admin.ModelAdmin):
    list_display = ("created_at", "admin", "point", "result", "distance", "accuracy_m", "ip_address")
    list_filter = ("result", "point")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="До точки")
    def distance(self, obj):
        return f"{obj.distance_m:.0f} м" if obj.distance_m is not None else "—"
