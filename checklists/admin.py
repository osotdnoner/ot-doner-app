from django.contrib import admin
from django.utils.html import format_html

from .models import ChecklistTemplate, Item, ItemResult, Run, Section, StaffCheck, Task


class SectionInline(admin.TabularInline):
    model = Section
    extra = 0
    fields = ("title", "order", "per_toilet", "includes_staff")
    show_change_link = True


@admin.register(ChecklistTemplate)
class ChecklistTemplateAdmin(admin.ModelAdmin):
    list_display = ("title", "code", "order", "is_active")
    inlines = [SectionInline]


class ItemInline(admin.StackedInline):
    model = Item
    extra = 0
    fields = (("title", "order", "photo_rule", "is_active"), "hint", ("problems", "critical_problems"))


@admin.register(Section)
class SectionAdmin(admin.ModelAdmin):
    list_display = ("title", "template", "order", "per_toilet", "includes_staff")
    list_filter = ("template",)
    inlines = [ItemInline]


def photo_thumb(image):
    if not image:
        return "—"
    return format_html('<a href="{0}" target="_blank"><img src="{0}" style="height:48px;border-radius:6px"></a>', image.url)


class ItemResultInline(admin.TabularInline):
    model = ItemResult
    extra = 0
    can_delete = False
    fields = ("label", "status", "problem", "comment", "thumb", "answered_at")
    readonly_fields = fields

    @admin.display(description="Фото")
    def thumb(self, obj):
        return photo_thumb(obj.photo)

    def has_add_permission(self, request, obj=None):
        return False


class StaffCheckInline(admin.TabularInline):
    model = StaffCheck
    extra = 0
    can_delete = False
    fields = ("employee", "attendance", "uniform_ok", "uniform_missing", "not_admitted", "comment", "thumb")
    readonly_fields = fields

    @admin.display(description="Фото")
    def thumb(self, obj):
        return photo_thumb(obj.photo)

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Run)
class RunAdmin(admin.ModelAdmin):
    list_display = ("template", "shift", "started_at", "finished_at", "too_fast")
    list_filter = ("template", "too_fast", "shift__point")
    readonly_fields = ("shift", "template", "started_at", "finished_at", "too_fast", "random_photo_keys")
    inlines = [ItemResultInline, StaffCheckInline]

    def has_add_permission(self, request):
        return False


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "point", "is_critical", "responsible", "deadline", "status", "before", "after")
    list_filter = ("status", "is_critical", "point")
    search_fields = ("title",)
    readonly_fields = ("point", "shift", "result", "created_at", "done_at", "done_by", "before", "after")

    @admin.display(description="Фото до")
    def before(self, obj):
        return photo_thumb(obj.result.photo if obj.result else None)

    @admin.display(description="Фото после")
    def after(self, obj):
        return photo_thumb(obj.photo_after)

    def has_add_permission(self, request):
        return False
