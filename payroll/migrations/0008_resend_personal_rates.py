"""Таблица Google перестроена (личные ставки, переработка и недоработка) — всё отправляем туда заново."""

from django.db import migrations


def resend(apps, schema_editor):
    apps.get_model("payroll", "Attendance").objects.update(needs_sync=True)
    apps.get_model("payroll", "Advance").objects.update(needs_sync=True)


class Migration(migrations.Migration):
    dependencies = [("payroll", "0007_extra_hours")]
    operations = [migrations.RunPython(resend, migrations.RunPython.noop)]
