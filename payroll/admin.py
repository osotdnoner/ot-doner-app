from django.contrib import admin, messages
from django.shortcuts import redirect
from django.urls import path
from django.utils import timezone

from . import sheets
from .models import Advance, Attendance, Bonus, ExtraHours, FineRules, LateFineTier, Penalty


class LateFineTierInline(admin.TabularInline):
    model = LateFineTier
    extra = 1


@admin.register(FineRules)
class FineRulesAdmin(admin.ModelAdmin):
    inlines = [LateFineTierInline]
    fields = ("absence_fine", "fine_admins")

    def has_add_permission(self, request):
        return not FineRules.objects.exists()

    def has_delete_permission(self, request, obj=None):
        return False

    def changelist_view(self, request, extra_context=None):
        # Запись одна — сразу открываем её
        return redirect("admin:payroll_finerules_change", FineRules.current().pk)


class BonusInline(admin.TabularInline):
    model = Bonus
    extra = 0
    fields = ("amount", "reason", "created_by", "created_at")
    readonly_fields = ("created_by", "created_at")


class PenaltyInline(BonusInline):
    model = Penalty


class ExtraHoursInline(admin.TabularInline):
    model = ExtraHours
    fk_name = "attendance"
    extra = 0
    fields = ("kind", "hours", "reason", "created_by", "created_at")
    readonly_fields = ("created_by", "created_at")


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = (
        "shift_date", "point", "employee", "status", "late_minutes", "fines", "bonus", "hours", "substitute", "in_sheet",
    )
    list_filter = ("status", "shift__point", "employee__position", "needs_sync")
    search_fields = ("employee__full_name", "reason")
    date_hierarchy = "shift__planned_start"
    list_select_related = ("shift__point", "employee")
    inlines = [PenaltyInline, BonusInline, ExtraHoursInline]
    actions = ["send_to_sheet"]
    fields = (
        "shift", "employee", "status", "kind", "position", "substitute_for", "arrived_at", "late_minutes", "reason",
        "fine", "fine_note", "fine_manual", "marked_by", "marked_at", "synced_at", "sync_error",
    )
    readonly_fields = (
        "shift", "employee", "status", "kind", "substitute_for", "arrived_at", "late_minutes", "fine_manual",
        "marked_by", "marked_at", "synced_at", "sync_error",
    )

    change_list_template = "admin/payroll/attendance/change_list.html"

    def has_add_permission(self, request):
        return False  # выходы отмечает администратор в приложении

    def get_urls(self):
        extra = [path("check-sheet/", self.admin_site.admin_view(self.check_sheet), name="payroll_check_sheet")]
        return extra + super().get_urls()

    def check_sheet(self, request):
        ok, error = sheets.check_connection()
        if ok:
            self.message_user(request, "Связь с Google Таблицей есть, секретное слово совпадает.", messages.SUCCESS)
        else:
            self.message_user(request, f"Нет связи с Google Таблицей. {error}", messages.ERROR)
        return redirect("admin:payroll_attendance_changelist")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("bonuses", "penalties", "extra_hours")

    @admin.display(description="Дата", ordering="shift__planned_start")
    def shift_date(self, obj):
        return f"{timezone.localtime(obj.shift.planned_start):%d.%m.%Y} {obj.shift.get_kind_display().lower()}"

    @admin.display(description="Точка", ordering="shift__point__name")
    def point(self, obj):
        return obj.shift.point

    @admin.display(description="Бонус, ₸")
    def bonus(self, obj):
        return obj.bonus_total or "—"

    @admin.display(description="Штраф, ₸")
    def fines(self, obj):
        return obj.fine_total or "—"

    @admin.display(description="Часы ±")
    def hours(self, obj):
        parts = []
        if obj.overtime_hours:
            parts.append(f"+{obj.overtime_hours} ч")
        if obj.undertime_hours:
            parts.append(f"−{obj.undertime_hours} ч")
        return " ".join(parts) or "—"

    @admin.display(description="Замена")
    def substitute(self, obj):
        return obj.substitute_text.replace("Замена: ", "") or "—"

    @admin.display(description="В таблице", boolean=True)
    def in_sheet(self, obj):
        return not obj.needs_sync

    def save_model(self, request, obj, form, change):
        if "fine" in form.changed_data:
            obj.fine_manual = True  # шеф поменял штраф — больше не пересчитываем
        obj.needs_sync = True
        super().save_model(request, obj, form, change)

    def save_related(self, request, form, formsets, change):
        from .services import remove_hours

        for formset in formsets:  # бонусы, штрафы, часы
            for item in formset.save(commit=False):
                if not item.created_by_id:
                    item.created_by = request.user
                item.save()
            for item in formset.deleted_objects:
                if isinstance(item, ExtraHours):
                    remove_hours(item)  # и вторую половину связки «кто задержался»
                else:
                    item.delete()
        form.instance.needs_sync = True
        Attendance.objects.filter(pk=form.instance.pk).update(needs_sync=True, marked_at=timezone.now())
        sheets.queue([form.instance.pk])

    def delete_model(self, request, obj):
        key = sheets.shift_key(obj)
        super().delete_model(request, obj)
        sheets.queue(deletes=[key])

    def delete_queryset(self, request, queryset):
        keys = [sheets.shift_key(a) for a in queryset]
        super().delete_queryset(request, queryset)
        sheets.queue(deletes=keys)

    @admin.action(description="Отправить в Google Таблицу ещё раз")
    def send_to_sheet(self, request, queryset):
        ids = list(queryset.values_list("id", flat=True))
        queryset.update(needs_sync=True)
        sent, error = sheets.push(ids)
        if error:
            self.message_user(request, f"Не ушло: {error}", messages.ERROR)
        else:
            self.message_user(request, f"Отправлено строк: {sent}.", messages.SUCCESS)


@admin.register(Advance)
class AdvanceAdmin(admin.ModelAdmin):
    """Авансы. Админ записывает их на смене, шеф — здесь. В таблице аванс вычитается из суммы к выплате."""

    list_display = ("given_on", "employee", "amount", "point", "comment", "given_by", "in_sheet")
    list_filter = ("point", "needs_sync")
    search_fields = ("employee__full_name", "comment")
    date_hierarchy = "given_on"
    autocomplete_fields = ("employee",)
    fields = ("employee", "amount", "given_on", "point", "comment", "given_by", "shift", "synced_at", "sync_error")
    readonly_fields = ("given_by", "shift", "synced_at", "sync_error")

    @admin.display(description="В таблице", boolean=True)
    def in_sheet(self, obj):
        return not obj.needs_sync

    def save_model(self, request, obj, form, change):
        if not obj.given_by_id:
            obj.given_by = request.user
        if not obj.point_id:
            obj.point = obj.employee.point
        obj.needs_sync = True
        super().save_model(request, obj, form, change)
        sheets.queue(advances=[obj.id])

    def delete_model(self, request, obj):
        key = sheets.advance_key(obj)
        super().delete_model(request, obj)
        sheets.queue(deletes=[key])

    def delete_queryset(self, request, queryset):
        keys = [sheets.advance_key(a) for a in queryset]
        super().delete_queryset(request, queryset)
        sheets.queue(deletes=keys)
