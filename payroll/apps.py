from django.apps import AppConfig


class PayrollConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "payroll"
    verbose_name = "Зарплата"

    def ready(self):
        from django.db.models.signals import post_save

        from staff.models import Employee

        from . import sheets

        def staff_changed(sender, **kwargs):
            # Новый сотрудник или новое имя — сразу на лист «Ставки», чтобы шеф вписал ставку
            sheets.queue_staff()

        post_save.connect(staff_changed, sender=Employee, dispatch_uid="payroll_staff_to_sheet")
