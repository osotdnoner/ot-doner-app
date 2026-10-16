from django.contrib import admin

from .models import Point


@admin.register(Point)
class PointAdmin(admin.ModelAdmin):
    list_display = ("name", "address", "radius_m", "day_shift_start", "night_shift_start", "late_grace_minutes", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "address")
    fieldsets = (
        (None, {"fields": ("name", "address", "is_active")}),
        ("Территория", {
            "fields": ("latitude", "longitude", "radius_m"),
            "description": "Координаты входа: в 2ГИС или Google Maps нажмите на место правой кнопкой "
                           "и скопируйте два числа, например 43.238949, 76.889709.",
        }),
        ("Смены", {"fields": ("timezone", "day_shift_start", "night_shift_start", "late_grace_minutes")}),
    )
