from django.db import models


class Employee(models.Model):
    """Сотрудник точки. Аккаунта в приложении у него нет — администратор выбирает его из списка."""

    class Position(models.TextChoices):
        CASHIER = "cashier", "Кассир"
        RUNNER = "runner", "Раннер"
        DONER = "doner", "Донерщик"
        PIZZA = "pizza", "Пиццерист"
        COOK = "cook", "Повар горячего цеха"
        OPERATOR = "operator", "Оператор"
        CLEANER = "cleaner", "Технический персонал"
        OTHER = "other", "Другое"

    point = models.ForeignKey("points.Point", verbose_name="Точка", on_delete=models.PROTECT, related_name="employees")
    full_name = models.CharField("Имя и фамилия", max_length=120)
    position = models.CharField("Должность", max_length=20, choices=Position.choices)
    phone = models.CharField("Телефон", max_length=20, blank=True)
    is_active = models.BooleanField("Работает", default=True)

    class Meta:
        verbose_name = "Сотрудник"
        verbose_name_plural = "Сотрудники"
        ordering = ["point", "position", "full_name"]

    def __str__(self) -> str:
        return self.full_name
