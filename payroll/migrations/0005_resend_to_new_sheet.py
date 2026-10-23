"""Таблица Google перестроена (журнал с авансами и заменами) — всё отправляем туда заново."""

from django.db import migrations


def resend(apps, schema_editor):
    apps.get_model("payroll", "Attendance").objects.update(needs_sync=True)


class Migration(migrations.Migration):
    dependencies = [("payroll", "0004_substitutes_penalties_advances")]
    operations = [migrations.RunPython(resend, migrations.RunPython.noop)]
