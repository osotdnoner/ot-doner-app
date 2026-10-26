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
    day_shift_start = models.TimeField("Дневная смена: начало", default=time(10, 0))
    day_shift_end = models.TimeField("Дневная смена: конец", default=time(22, 0))
    has_night_shift = models.BooleanField("Есть ночная смена", default=True)
    night_shift_start = models.TimeField("Ночная смена: начало", default=time(22, 0))
    night_shift_end = models.TimeField(
        "Ночная смена: конец", default=time(10, 0),
        help_text="Если конец раньше начала (например 18:00–02:00), смена заканчивается на следующий день. "
                  "Полная смена — с начала дневной до конца ночной.",
    )
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

    def shift_hours(self, kind: str) -> float:
        """Длина смены в часах: day — дневная, night — ночная, full — с начала дневной до конца ночной."""
        def length(start, end):
            minutes = (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)
            return (minutes % (24 * 60)) or 24 * 60  # через полночь; 10:00–10:00 — сутки

        if kind == "night" and self.has_night_shift:
            minutes = length(self.night_shift_start, self.night_shift_end)
        elif kind == "full" and self.has_night_shift:
            minutes = length(self.day_shift_start, self.night_shift_end)
        else:
            minutes = length(self.day_shift_start, self.day_shift_end)
        return round(minutes / 60, 2)

    def schedule_text(self) -> str:
        day = f"день {self.day_shift_start:%H:%M}–{self.day_shift_end:%H:%M}"
        if not self.has_night_shift:
            return day
        return f"{day}, ночь {self.night_shift_start:%H:%M}–{self.night_shift_end:%H:%M}"
