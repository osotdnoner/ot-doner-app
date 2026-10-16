from django.conf import settings
from django.db import models


class Shift(models.Model):
    """Смена администратора. Создаётся в момент отметки «Я пришёл»."""

    class Kind(models.TextChoices):
        DAY = "day", "Дневная"
        NIGHT = "night", "Ночная"

    class Status(models.TextChoices):
        OPEN = "open", "Идёт"
        CLOSED = "closed", "Передана"

    point = models.ForeignKey("points.Point", verbose_name="Точка", on_delete=models.PROTECT, related_name="shifts")
    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Администратор", on_delete=models.PROTECT, related_name="shifts",
    )
    kind = models.CharField("Смена", max_length=10, choices=Kind.choices)
    planned_start = models.DateTimeField("Начало по графику")
    planned_end = models.DateTimeField("Конец по графику")

    # Всё ниже записывает сервер в момент отметки — телефон эти данные не задаёт
    checked_in_at = models.DateTimeField("Отметился (время сервера)")
    latitude = models.DecimalField("Широта", max_digits=9, decimal_places=6)
    longitude = models.DecimalField("Долгота", max_digits=9, decimal_places=6)
    accuracy_m = models.FloatField("Точность GPS, м")
    distance_m = models.FloatField("Расстояние до точки, м")
    selfie = models.ImageField("Селфи", upload_to="selfies/%Y/%m/%d/")

    late_minutes = models.PositiveIntegerField("Опоздание, мин", default=0)
    late_reason = models.TextField("Причина опоздания", blank=True)

    status = models.CharField("Статус", max_length=10, choices=Status.choices, default=Status.OPEN)

    class Meta:
        verbose_name = "Смена"
        verbose_name_plural = "Смены"
        ordering = ["-planned_start"]
        constraints = [
            models.UniqueConstraint(fields=["admin", "planned_start"], name="one_checkin_per_admin_per_shift"),
        ]

    def __str__(self) -> str:
        return f"{self.point} · {self.get_kind_display()} · {self.admin}"

    @property
    def is_late(self) -> bool:
        return self.late_minutes > 0

    @property
    def late_human(self) -> str:
        """Опоздание по-человечески: «15 мин» или «3 ч 7 мин»."""
        hours, minutes = divmod(self.late_minutes, 60)
        if not hours:
            return f"{minutes} мин"
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"

    @property
    def needs_late_reason(self) -> bool:
        return self.is_late and not self.late_reason


class CheckinAttempt(models.Model):
    """Каждое нажатие «Я пришёл», в том числе неудачное. Помогает заметить попытки обмана."""

    class Result(models.TextChoices):
        OK = "ok", "Отметка принята"
        OUTSIDE = "outside", "Вне территории"
        LOW_ACCURACY = "low_accuracy", "Слабый GPS"
        NO_SLOT = "no_slot", "Нет смены в это время"
        ALREADY = "already", "Уже отмечен"
        NO_POINT = "no_point", "Нет закреплённых точек"

    admin = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Администратор", on_delete=models.CASCADE, related_name="checkin_attempts",
    )
    point = models.ForeignKey(
        "points.Point", verbose_name="Ближайшая точка", on_delete=models.SET_NULL, null=True, blank=True,
    )
    created_at = models.DateTimeField("Время", auto_now_add=True)
    latitude = models.DecimalField("Широта", max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField("Долгота", max_digits=9, decimal_places=6, null=True, blank=True)
    accuracy_m = models.FloatField("Точность GPS, м", null=True, blank=True)
    distance_m = models.FloatField("Расстояние до точки, м", null=True, blank=True)
    result = models.CharField("Результат", max_length=20, choices=Result.choices)
    ip_address = models.GenericIPAddressField("IP-адрес", null=True, blank=True)

    class Meta:
        verbose_name = "Попытка отметки"
        verbose_name_plural = "Попытки отметки"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.admin} · {self.get_result_display()}"


class MissedCheckinAlert(models.Model):
    """Запоминает, что управляющему уже сообщили «на точке никто не отметился», чтобы не повторять."""

    point = models.ForeignKey("points.Point", verbose_name="Точка", on_delete=models.CASCADE)
    planned_start = models.DateTimeField("Начало смены")
    created_at = models.DateTimeField("Сообщено", auto_now_add=True)

    class Meta:
        verbose_name = "Сообщение «никто не отметился»"
        verbose_name_plural = "Сообщения «никто не отметился»"
        constraints = [models.UniqueConstraint(fields=["point", "planned_start"], name="one_missed_alert_per_shift")]
