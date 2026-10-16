from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "get_full_name", "role", "points_list", "is_active")
    list_filter = ("role", "is_active", "points")
    search_fields = ("username", "first_name", "last_name")
    filter_horizontal = ("points", "groups", "user_permissions")

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Человек", {"fields": ("first_name", "last_name", "role", "points", "telegram_chat_id")}),
        ("Доступ", {"fields": ("is_active", "is_staff", "is_superuser"), "classes": ("collapse",)}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("username", "first_name", "last_name", "role", "password1", "password2"),
            "description": "Логин — номер телефона, например 87011234567.",
        }),
    )

    @admin.display(description="Точки")
    def points_list(self, obj):
        return ", ".join(p.name for p in obj.points.all()) or "—"


# Группы прав пока не нужны — убираем лишний раздел из панели
from django.contrib.auth.models import Group  # noqa: E402

admin.site.unregister(Group)
