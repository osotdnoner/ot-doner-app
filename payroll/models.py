from django.conf import settings
from django.db import models


class FineRules(models.Model):
    """Правила штрафов. Запись одна на всю сеть, шеф меняет суммы в панели."""

    absence_fine = models.PositiveIntegerField(
        "Штраф за невыход, ₸", default=0,
        help_text="0 — автоматически не штрафовать. Штраф конкретному человеку можно поставить или снять в «Выходах на смену».",
    )
    fine_admins = models.BooleanField(
        "Штрафовать администраторов за опоздание", default=True,
        help_text="По тем же ступеням, что и сотрудников. Опоздание считается по отметке «Я пришёл».",
    )

    class Meta:
        verbose_name = "Правила штрафов"
        verbose_name_plural = "Правила штрафов"

    def __str__(self) -> str:
        return "Правила штрафов"

    @classmethod
    def current(cls) -> "FineRules":
        rules, _ = cls.objects.get_or_create(pk=1)
        return rules

    def late_fine(self, minutes: int) -> int:
        """Штраф за опоздание: сумма самой старшей ступени, до которой дотянуло опоздание."""
        if minutes <= 0:
            return 0
        tier = self.tiers.filter(minutes_from__lte=minutes).order_by("-minutes_from").first()
        return tier.amount if tier else 0


class LateFineTier(models.Model):
    rules = models.ForeignKey(FineRules, on_delete=models.CASCADE, related_name="tiers")
    minutes_from = models.PositiveSmallIntegerField("Опоздание от, мин")
    amount = models.PositiveIntegerField("Штраф, ₸")

    class Meta:
        verbose_name = "Ступень штрафа"
        verbose_name_plural = "Штраф за опоздание по ступеням"
        ordering = ["minutes_from"]
        constraints = [models.UniqueConstraint(fields=["rules", "minutes_from"], name="unique_fine_tier")]

    def __str__(self) -> str:
        return f"от {self.minutes_from} мин — {self.amount} ₸"


class Attendance(models.Model):
    """Выход сотрудника на смену: пришёл ли, опоздание, причина, штраф. Одна строка в зарплатной таблице."""

    class Status(models.TextChoices):
        ON_TIME = "on_time", "Пришёл вовремя"
        LATE = "late", "Опоздал"
        ABSENT = "absent", "Не вышел"
        OFF = "off", "Выходной"  # не его смена: в зарплату не идёт

    class Kind(models.TextChoices):
        DAY = "day", "День"
        NIGHT = "night", "Ночь"
        FULL = "full", "Полная"

    WORKED = (Status.ON_TIME, Status.LATE)

    shift = models.ForeignKey("shifts.Shift", verbose_name="Смена", on_delete=models.CASCADE, related_name="attendance")
    employee = models.ForeignKey(
        "staff.Employee", verbose_name="Сотрудник", on_delete=models.PROTECT, related_name="attendance",
    )
    status = models.CharField("Выход", max_length=10, choices=Status.choices)
    kind = models.CharField(
        "Смена сотрудника", max_length=10, choices=Kind.choices, default=Kind.DAY,
        help_text="Полная — с начала дневной до конца ночной смены",
    )
    arrived_at = models.DateTimeField("Пришёл в", null=True, blank=True)
    late_minutes = models.PositiveIntegerField("Опоздание, мин", default=0)
    reason = models.TextField("Причина опоздания или невыхода", blank=True)

    fine = models.PositiveIntegerField("Штраф, ₸", default=0)
    fine_manual = models.BooleanField(
        "Штраф изменён шефом", default=False,
        help_text="Если шеф поменял штраф вручную, приложение его больше не пересчитывает",
    )
    fine_note = models.CharField("Комментарий к штрафу", max_length=200, blank=True)

    marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Кто отметил", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    marked_at = models.DateTimeField("Когда отмечено", auto_now=True)

    # Выгрузка в Google Таблицу
    needs_sync = models.BooleanField("Ждёт выгрузки", default=True)
    synced_at = models.DateTimeField("Выгружено в таблицу", null=True, blank=True)
    sync_error = models.TextField("Ошибка выгрузки", blank=True)

    class Meta:
        verbose_name = "Выход на смену"
        verbose_name_plural = "Выходы на смену"
        ordering = ["-shift__planned_start", "employee__full_name"]
        constraints = [models.UniqueConstraint(fields=["shift", "employee"], name="unique_attendance")]

    def __str__(self) -> str:
        return f"{self.employee} · {self.get_status_display()}"

    @property
    def bonus_total(self) -> int:
        return sum(b.amount for b in self.bonuses.all())

    @property
    def late_human(self) -> str:
        hours, minutes = divmod(self.late_minutes, 60)
        if hours:
            return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
        return f"{minutes} мин"


class Bonus(models.Model):
    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="bonuses", verbose_name="Выход")
    amount = models.PositiveIntegerField("Сумма, ₸")
    reason = models.CharField("За что", max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Кто назначил", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField("Когда", auto_now_add=True)

    class Meta:
        verbose_name = "Бонус"
        verbose_name_plural = "Бонусы"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.amount} ₸ — {self.reason}"
