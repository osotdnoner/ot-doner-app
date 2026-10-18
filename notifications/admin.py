from django.contrib import admin, messages

from .models import Notification
from .service import send_now


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "event", "recipient", "channel", "state", "short_error")
    list_filter = ("state", "channel", "event", "point")
    search_fields = ("title", "text", "recipient__first_name", "recipient__username")
    date_hierarchy = "created_at"
    actions = ["resend"]
    fields = (
        "created_at", "event", "point", "recipient", "channel", "address", "title", "text",
        "state", "error", "attempts", "sent_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="Причина ошибки")
    def short_error(self, obj):
        return (obj.error[:60] + "…") if len(obj.error) > 60 else (obj.error or "—")

    @admin.action(description="Отправить ещё раз")
    def resend(self, request, queryset):
        sent = failed = 0
        for notification in queryset.exclude(channel=""):
            if send_now(notification).ok:
                sent += 1
            else:
                failed += 1
        if sent:
            self.message_user(request, f"Отправлено: {sent}.", messages.SUCCESS)
        if failed:
            self.message_user(request, f"Не ушло: {failed}. Причина — в колонке «Причина ошибки».", messages.WARNING)
