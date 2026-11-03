import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def normalize_kz_phone(value: str) -> str:
    """'8 777 123 45 67', '+77771234567', '7771234567' → '+7 777 123 45 67'. Пустое — пустое."""
    digits = re.sub(r"\D", "", value or "")
    if not digits:
        return ""
    if len(digits) == 10 and digits.startswith("7"):
        digits = "7" + digits
    if len(digits) == 11 and digits[0] == "8":
        digits = "7" + digits[1:]
    # Мобильные в Казахстане: +7 70x, 74x–77x. +7 71x–73x — городские (Астана, Алматы…), Kaspi на них не бывает
    if len(digits) != 11 or not digits.startswith("77") or digits[2] in "123":
        raise ValidationError("Номер Kaspi — казахстанский мобильный, например +7 777 123 45 67.")
    return f"+7 {digits[1:4]} {digits[4:7]} {digits[7:9]} {digits[9:11]}"


class Employee(models.Model):
    """
    Сотрудник точки. Аккаунта в приложении у него нет — администратор отмечает его из списка.
    Администраторы смены тоже есть в этом списке (должность «Администратор», связь с их аккаунтом),
    чтобы их опоздания и штрафы попадали в зарплату вместе со всеми.
    """

    class Position(models.TextChoices):
        ADMIN = "admin", "Администратор"
        DONER = "doner", "Донерщик"
        PIZZA = "pizza", "Пиццерист"
        CASHIER = "cashier", "Кассир"
        RUNNER_DELIVERY = "runner_delivery", "Раннер доставки"
        RUNNER = "runner", "Раннер кассы"
        CLEANER = "cleaner", "Техперсонал"
        CLEANER_KITCHEN = "cleaner_kitchen", "Техперсонал кухни"
        CLEANER_HALL = "cleaner_hall", "Техперсонал зала"
        COOK = "cook", "Повар горячего цеха"
        OPERATOR = "operator", "Оператор"
        OTHER = "other", "Другое"

    point = models.ForeignKey(
        "points.Point", verbose_name="Филиал", on_delete=models.PROTECT, related_name="employees",
        null=True, blank=True,
        help_text="Пусто — человек в резерве (выручалка): его можно вызвать на замену на любой филиал",
    )
    full_name = models.CharField("Имя и фамилия", max_length=120)
    position = models.CharField("Должность", max_length=20, choices=Position.choices)
    phone = models.CharField("Телефон", max_length=20, blank=True)
    is_active = models.BooleanField("Работает", default=True)
    kaspi_phone = models.CharField(
        "Kaspi Gold: номер телефона", max_length=20, blank=True,
        help_text="Номер, на который переводим зарплату. Например: +7 777 123 45 67",
    )
    kaspi_name = models.CharField(
        "Kaspi Gold: имя получателя", max_length=80, blank=True,
        help_text="Как Kaspi показывает получателя при переводе по этому номеру, например «Ержан Б.». "
                  "Бухгалтер сверяет его перед отправкой денег.",
    )
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, verbose_name="Аккаунт в приложении", on_delete=models.SET_NULL,
        null=True, blank=True, related_name="employee",
        help_text="Только для администраторов смены: связывает отметку «Я пришёл» с зарплатой",
    )

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"
        ordering = ["point", "position", "full_name"]

    def __str__(self) -> str:
        return self.full_name

    def clean(self):
        super().clean()
        self.kaspi_phone = normalize_kz_phone(self.kaspi_phone)
        self.kaspi_name = " ".join(self.kaspi_name.split())
        if self.kaspi_phone and not self.kaspi_name:
            raise ValidationError({"kaspi_name": "Впишите, какое имя Kaspi показывает по этому номеру."})
        if self.kaspi_name and not self.kaspi_phone:
            raise ValidationError({"kaspi_phone": "Впишите номер Kaspi Gold."})

    @property
    def home(self) -> str:
        """Родной филиал или «Резерв»."""
        return self.point.name if self.point_id else "Резерв"
