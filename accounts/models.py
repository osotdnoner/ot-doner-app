from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models

from notifications.whatsapp import normalize_phone


class User(AbstractUser):
    """Пользователь приложения. Логин — номер телефона."""

    class Role(models.TextChoices):
        ADMIN = "admin", "Администратор смены"
        MANAGER = "manager", "Управляющий"
        OWNER = "owner", "Собственник"

    role = models.CharField("Роль", max_length=20, choices=Role.choices, default=Role.ADMIN)
    points = models.ManyToManyField(
        "points.Point", verbose_name="Точки", blank=True, related_name="users",
        help_text="Администратор отмечается только на своих точках. Управляющий и собственник получают "
                  "уведомления по своим точкам; собственник без выбранных точек — по всем.",
    )

    # --- Куда присылать уведомления ---
    telegram_chat_id = models.CharField(
        "Telegram chat ID", max_length=32, blank=True,
        help_text="Число, которое показывает бот @userinfobot. Перед этим нужно открыть нашего бота и нажать «Старт».",
    )
    whatsapp_phone = models.CharField(
        "Номер WhatsApp", max_length=20, blank=True,
        help_text="Например 87011234567 — на этот номер придут уведомления в WhatsApp.",
    )

    # --- О чём присылать ---
    notify_late = models.BooleanField("Опоздания администраторов", default=True)
    notify_problems = models.BooleanField(
        "Неполадки при приёме смены", default=True,
        help_text="Одно сообщение со всеми проблемами, когда администратор завершает осмотр зала.",
    )
    notify_urgent = models.BooleanField(
        "Срочное — сразу", default=True,
        help_text="Критичные проблемы (нет воды, запах газа…), сотрудник не вышел или не допущен к работе.",
    )
    notify_reminders = models.BooleanField(
        "Напоминания", default=True,
        help_text="Просроченные задачи и смены, на которые никто не отметился.",
    )

    class Meta:
        verbose_name = "Пользователь"
        verbose_name_plural = "Пользователи"

    def __str__(self) -> str:
        return self.get_full_name() or self.username

    def clean(self):
        super().clean()
        self.telegram_chat_id = self.telegram_chat_id.strip()
        if self.whatsapp_phone:
            self.whatsapp_phone = normalize_phone(self.whatsapp_phone)
            if not 10 <= len(self.whatsapp_phone) <= 15:
                raise ValidationError({"whatsapp_phone": "Укажите номер полностью, например 87011234567."})

    @property
    def is_shift_admin(self) -> bool:
        return self.role == self.Role.ADMIN

    def notification_addresses(self) -> list[tuple[str, str]]:
        """Куда отправлять: [("telegram", chat_id), ("whatsapp", номер)] — только заполненные."""
        addresses = []
        if self.telegram_chat_id:
            addresses.append(("telegram", self.telegram_chat_id))
        if self.whatsapp_phone:
            addresses.append(("whatsapp", self.whatsapp_phone))
        return addresses
