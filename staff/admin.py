from django.contrib import admin

from .models import Employee


class KaspiFilter(admin.SimpleListFilter):
    title = "Kaspi Gold"
    parameter_name = "kaspi"

    def lookups(self, request, model_admin):
        return [("yes", "Заполнен"), ("no", "Не заполнен")]

    def queryset(self, request, queryset):
        if self.value() == "yes":
            return queryset.exclude(kaspi_phone="")
        if self.value() == "no":
            return queryset.filter(kaspi_phone="")
        return queryset


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("full_name", "position", "home_point", "phone", "kaspi", "is_active", "added")
    list_filter = ("is_active", "point", "position", KaspiFilter)
    search_fields = ("full_name", "phone", "kaspi_phone", "kaspi_name")
    autocomplete_fields = ("user",)
    fieldsets = (
        (None, {"fields": ("full_name", "point", "position", "phone", "is_active")}),
        ("Кто добавил и когда ушёл", {"fields": ("added_by", "created_at", "left_at", "left_by")}),
        ("Kaspi Gold — для выплаты зарплаты", {
            "fields": ("kaspi_phone", "kaspi_name"),
            "description": "Попадает в Google Таблицу на листы «Сотрудники» и «Выплата»: бухгалтер видит номер "
                           "и имя получателя рядом с суммой к выплате.",
        }),
        ("Аккаунт администратора", {"fields": ("user",), "classes": ("collapse",)}),
    )

    readonly_fields = ("added_by", "created_at", "left_by")

    @admin.display(description="Добавлен", ordering="created_at")
    def added(self, obj):
        who = f" · {obj.added_by}" if obj.added_by_id else ""
        left = f" · ушёл {obj.left_at:%d.%m.%Y}" if obj.left_at else ""
        return f"{obj.created_at:%d.%m.%Y}{who}{left}"

    def save_model(self, request, obj, form, change):
        if not change and not obj.added_by_id:
            obj.added_by = request.user
        if obj.is_active:  # работает (в том числе вернули) — отметку об уходе снимаем
            obj.left_at, obj.left_by = None, None
        elif not obj.is_active and not obj.left_at:
            from django.utils import timezone
            obj.left_at, obj.left_by = timezone.localdate(), request.user
        super().save_model(request, obj, form, change)

    @admin.display(description="Филиал", ordering="point__name")
    def home_point(self, obj):
        return obj.home

    @admin.display(description="Kaspi Gold", ordering="kaspi_phone")
    def kaspi(self, obj):
        return f"{obj.kaspi_phone} · {obj.kaspi_name}" if obj.kaspi_phone else "—"
