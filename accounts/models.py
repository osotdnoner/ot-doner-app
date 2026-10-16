from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """Пользователь приложения. Логин — номер телефона."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Администратор смены"
        MANAGER = "manager", "Управляющий"
        OWNER = "owner", "Собственник"

    role = models.CharField("Роль", max_length=20, choices=Role.choices, default=Role.ADMIN)
    points = models.ManyToManyField(
        "points.Point", verbose_name="Точки", blank=True, related_name="users",
        help_text="Администратор отмечается только на своих точках; "
                  "управляющий получает уведомления по своим точкам",
    )
    telegram_chat_id = models.CharField(
        "Telegram chat ID", max_length=32, blank=True,
        help_text="Куда бот отправляет уведомления этому человеку",
    )

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    @property
    def is_shift_admin(self) -> bool:
        return self.role == self.Role.ADMIN
