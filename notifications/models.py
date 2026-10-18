from django.conf import settings
from django.db import models
from django.utils import timezone


class Notification(models.Model):
    """Одно сообщение одному человеку в один мессенджер. Журнал виден в панели: «Уведомления»."""

    class Event(models.TextChoices):
        LATE = "late", "Опоздание администратора"
        PROBLEMS = "problems", "Неполадки при приёме смены"
        URGENT = "urgent", "Срочное"
        REMINDER = "reminder", "Напоминание"
        TEST = "test", "Проверка связи"

    class Channel(models.TextChoices):
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP = "whatsapp", "WhatsApp"

    class State(models.TextChoices):
        PENDING = "pending", "В очереди"
        SENT = "sent", "Отправлено"
        FAILED = "failed", "Ошибка"
        SKIPPED = "skipped", "Не отправлено"

    created_at = models.DateTimeField("Создано", default=timezone.now, db_index=True)
    event = models.CharField("Событие", max_length=20, choices=Event.choices)
    point = models.ForeignKey(
        "points.Point", verbose_name="Точка", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, verbose_name="Кому", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="notifications",
    )
    channel = models.CharField("Куда", max_length=20, choices=Channel.choices, blank=True)
    address = models.CharField("Chat ID или номер", max_length=64, blank=True)
    title = models.CharField("Заголовок", max_length=200)
    text = models.TextField("Текст")
    state = models.CharField("Статус", max_length=20, choices=State.choices, default=State.PENDING)
    error = models.TextField("Причина ошибки", blank=True)
    attempts = models.PositiveSmallIntegerField("Попыток", default=0)
    sent_at = models.DateTimeField("Отправлено", null=True, blank=True)

    class Meta:
        verbose_name = "Сообщение"
        verbose_name_plural = "Журнал сообщений"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.title} → {self.recipient or 'никому'}"

    @property
    def full_text(self) -> str:
        return f"{self.title}\n{self.text}"
