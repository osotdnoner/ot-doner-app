from datetime import time

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models


class Point(models.Model):
    """Точка «ОТ Донер» (ресторан). Заполняется в админ-панели."""

    name = models.CharField("Название", max_length=100, help_text="Например: «27» или «Астана»")
    address = models.CharField("Адрес", max_length=255, blank=True)

    # Координаты входа в ресторан. Проще всего взять из 2ГИС или Google Maps:
    # правый клик по точке на карте → скопировать координаты.
    latitude = models.DecimalField(
        "Широта", max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-90), MaxValueValidator(90)],
    )
    longitude = models.DecimalField(
        "Долгота", max_digits=9, decimal_places=6, null=True, blank=True,
        validators=[MinValueValidator(-180), MaxValueValidator(180)],
    )
    require_location = models.BooleanField(
        "Проверять территорию", default=False,
        help_text="Если включено, кнопка «Я пришёл» работает только в радиусе точки (нужна геолокация телефона)",
    )
    radius_m = models.PositiveIntegerField(
        "Радиус территории, м", default=100,
        help_text="В этом радиусе от точки кнопка «Я пришёл» работает",
    )

    timezone = models.CharField("Часовой пояс", max_length=50, default="Asia/Almaty")
    day_shift_start = models.TimeField("Начало дневной смены", default=time(10, 0))
    night_shift_start = models.TimeField("Начало ночной смены", default=time(22, 0))
    late_grace_minutes = models.PositiveSmallIntegerField(
        "Допуск по опозданию, мин", default=0,
        help_text="0 — опоздание считается с первой минуты",
    )

    toilets_count = models.PositiveSmallIntegerField(
        "Санузлов", default=1,
        help_text="Каждый санузел проверяется отдельно и со своим фото",
    )

    is_active = models.BooleanField("Работает", default=True)

    class Meta:
        verbose_name = "Точка"
        verbose_name_plural = "Точки"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def has_coordinates(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def clean(self):
        if self.require_location and not self.has_coordinates:
            raise ValidationError("Чтобы проверять территорию, укажите широту и долготу точки.")
