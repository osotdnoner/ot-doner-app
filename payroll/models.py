from django.conf import settings
from django.db import models
from django.utils import timezone

from staff.models import Employee


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
    position = models.CharField(
        "Должность на смене", max_length=20, blank=True, choices=Employee.Position.choices,
        help_text="Если человек вышел на замену и работал на другой должности. Пусто — его обычная должность.",
    )
    substitute_for = models.ForeignKey(
        "staff.Employee", verbose_name="Вместо кого", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="replaced_by",
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
    def penalty_total(self) -> int:
        return sum(p.amount for p in self.penalties.all())

    @property
    def fine_total(self) -> int:
        """Весь штраф за смену: за опоздание или невыход плюс штрафы во время смены."""
        return self.fine + self.penalty_total

    @property
    def fine_reasons(self) -> str:
        reasons = []
        if self.fine and self.status == self.Status.LATE:
            reasons.append(f"опоздание {self.late_human} — {self.fine}")
        elif self.fine and self.status == self.Status.ABSENT:
            reasons.append(f"невыход — {self.fine}")
        elif self.fine:
            reasons.append(f"штраф — {self.fine}")
        reasons += [f"{p.reason} — {p.amount}" for p in self.penalties.all()]
        return "; ".join(reasons)

    @property
    def is_substitute(self) -> bool:
        """Вышел на замену на чужой филиал (или из резерва)."""
        return self.employee.point_id != self.shift.point_id

    @property
    def position_code(self) -> str:
        return self.position or self.employee.position

    @property
    def position_display(self) -> str:
        return dict(Employee.Position.choices).get(self.position_code, self.position_code)

    @property
    def substitute_text(self) -> str:
        if not self.is_substitute and not self.substitute_for_id:
            return ""
        parts = []
        if self.substitute_for_id:
            parts.append(f"вместо {self.substitute_for}")
        if self.is_substitute:
            parts.append("из резерва" if not self.employee.point_id else f"с филиала {self.employee.point}")
        return "Замена: " + ", ".join(parts)

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


class Penalty(models.Model):
    """Штраф во время смены: за что и сколько. Ставит администратор или шеф."""

    attendance = models.ForeignKey(Attendance, on_delete=models.CASCADE, related_name="penalties", verbose_name="Выход")
    amount = models.PositiveIntegerField("Сумма, ₸")
    reason = models.CharField("За что", max_length=200)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Кто поставил", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField("Когда", auto_now_add=True)

    class Meta:
        verbose_name = "Штраф во время смены"
        verbose_name_plural = "Штрафы во время смены"
        ordering = ["created_at"]

    def __str__(self) -> str:
        return f"{self.amount} ₸ — {self.reason}"


class Advance(models.Model):
    """Аванс: деньги, выданные сотруднику заранее. Вычитается из зарплаты в таблице."""

    employee = models.ForeignKey("staff.Employee", verbose_name="Сотрудник", on_delete=models.PROTECT,
                                 related_name="advances")
    point = models.ForeignKey("points.Point", verbose_name="Где выдан", on_delete=models.PROTECT, null=True,
                              blank=True, related_name="+")
    given_on = models.DateField("Дата", default=timezone.localdate)
    amount = models.PositiveIntegerField("Сумма, ₸")
    comment = models.CharField("Комментарий", max_length=200, blank=True)
    given_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Кто выдал", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )
    shift = models.ForeignKey("shifts.Shift", verbose_name="Смена", on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="advances")
    created_at = models.DateTimeField("Записано", auto_now=True)

    needs_sync = models.BooleanField("Ждёт выгрузки", default=True)
    synced_at = models.DateTimeField("Выгружено в таблицу", null=True, blank=True)
    sync_error = models.TextField("Ошибка выгрузки", blank=True)

    class Meta:
        verbose_name = "Аванс"
        verbose_name_plural = "Авансы"
        ordering = ["-given_on", "-id"]

    def __str__(self) -> str:
        return f"{self.employee} — {self.amount} ₸"


class SheetDeletion(models.Model):
    """Строка, которую нужно убрать из Google Таблицы (удалили аванс или замену, отметили «Выходной»)."""

    key = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
