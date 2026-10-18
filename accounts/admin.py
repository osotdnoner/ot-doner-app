from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from notifications.service import send_test

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ("username", "get_full_name", "role", "points_list", "messengers", "is_active")
    list_filter = ("role", "is_active", "points")
    search_fields = ("username", "first_name", "last_name")
    filter_horizontal = ("points", "groups", "user_permissions")
    actions = ["send_test_message"]

    fieldsets = (
        (None, {"fields": ("username", "password")}),
        ("Человек", {"fields": ("first_name", "last_name", "role", "points")}),
        ("Уведомления (для управляющего и собственника)", {
            "fields": (
                "telegram_chat_id", "whatsapp_phone",
                "notify_late", "notify_problems", "notify_urgent", "notify_reminders",
            ),
            "description": "Проверить, что сообщения доходят: список пользователей → отметьте человека → "
                           "действие «Отправить тестовое сообщение».",
        }),
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

    @admin.display(description="Уведомления")
    def messengers(self, obj):
        names = {"telegram": "Telegram", "whatsapp": "WhatsApp"}
        return ", ".join(names[channel] for channel, _ in obj.notification_addresses()) or "—"

    @admin.action(description="Отправить тестовое сообщение")
    def send_test_message(self, request, queryset):
        for user in queryset:
            results = send_test(user)
            if not results:
                self.message_user(request, f"{user}: не указаны ни Telegram chat ID, ни номер WhatsApp.", messages.WARNING)
            for channel, outcome in results:
                if outcome.ok:
                    self.message_user(request, f"{user} · {channel}: отправлено.", messages.SUCCESS)
                else:
                    self.message_user(request, f"{user} · {channel}: не ушло. {outcome.error}", messages.ERROR)


# Группы прав пока не нужны — убираем лишний раздел из панели
from django.contrib.auth.models import Group  # noqa: E402

admin.site.unregister(Group)
