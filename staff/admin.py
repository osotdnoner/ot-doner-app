from django.contrib import admin

from .models import Employee


@admin.register(Employee)
class EmployeeAdmin(admin.ModelAdmin):
    list_display = ("full_name", "position", "home_point", "phone", "is_active")
    list_filter = ("point", "position", "is_active")
    search_fields = ("full_name", "phone")
    autocomplete_fields = ("user",)

    @admin.display(description="Филиал", ordering="point__name")
    def home_point(self, obj):
        return obj.home
