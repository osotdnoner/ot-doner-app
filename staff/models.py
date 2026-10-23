from django.conf import settings
from django.db import models


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

    point = models.ForeignKey("points.Point", verbose_name="Точка", on_delete=models.PROTECT, related_name="employees")
    full_name = models.CharField("Имя и фамилия", max_length=120)
    position = models.CharField("Должность", max_length=20, choices=Position.choices)
    phone = models.CharField("Телефон", max_length=20, blank=True)
    is_active = models.BooleanField("Работает", default=True)
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
