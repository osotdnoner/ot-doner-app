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
    list_display = ("full_name", "position", "home_point", "phone", "kaspi", "is_active")
    list_filter = ("point", "position", "is_active", KaspiFilter)
    search_fields = ("full_name", "phone", "kaspi_phone", "kaspi_name")
    autocomplete_fields = ("user",)
    fieldsets = (
        (None, {"fields": ("full_name", "point", "position", "phone", "is_active")}),
        ("Kaspi Gold — для выплаты зарплаты", {
            "fields": ("kaspi_phone", "kaspi_name"),
            "description": "Попадает в Google Таблицу на листы «Сотрудники» и «Выплата»: бухгалтер видит номер "
                           "и имя получателя рядом с суммой к выплате.",
        }),
        ("Аккаунт администратора", {"fields": ("user",), "classes": ("collapse",)}),
    )

    @admin.display(description="Филиал", ordering="point__name")
    def home_point(self, obj):
        return obj.home

    @admin.display(description="Kaspi Gold", ordering="kaspi_phone")
    def kaspi(self, obj):
        return f"{obj.kaspi_phone} · {obj.kaspi_name}" if obj.kaspi_phone else "—"
