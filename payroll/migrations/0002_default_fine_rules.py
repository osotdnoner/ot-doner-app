"""Стартовые правила штрафов. Шеф меняет суммы в панели: «Зарплата → Правила штрафов»."""

from django.db import migrations

DEFAULT_TIERS = [(1, 1000), (15, 3000), (60, 5000)]


def create_rules(apps, schema_editor):
    FineRules = apps.get_model("payroll", "FineRules")
    LateFineTier = apps.get_model("payroll", "LateFineTier")
    rules, created = FineRules.objects.get_or_create(pk=1)
    if created:
        for minutes, amount in DEFAULT_TIERS:
            LateFineTier.objects.create(rules=rules, minutes_from=minutes, amount=amount)


class Migration(migrations.Migration):
    dependencies = [("payroll", "0001_initial")]
    operations = [migrations.RunPython(create_rules, migrations.RunPython.noop)]
