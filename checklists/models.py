"""
Движок чек-листов.

Шаблоны (что проверять) хранятся в базе и правятся в панели управления — программист
для этого не нужен. Ответы (что нашёл администратор) хранят копию текста пункта,
поэтому старые отчёты не меняются, когда пункты редактируют.
"""

from django.conf import settings
from django.db import models


class ChecklistTemplate(models.Model):
    """Вкладка приёма смены: «Осмотр зала», «Кассовая зона» и т. д."""

    code = models.SlugField("Код", unique=True, help_text="Латиницей, например hall")
    title = models.CharField("Название", max_length=100)
    order = models.PositiveSmallIntegerField("Порядок", default=0)
    is_active = models.BooleanField("Используется", default=True)

    class Meta:
        verbose_name = "Чек-лист"
        verbose_name_plural = "Чек-листы"
        ordering = ["order"]

    def __str__(self) -> str:
        return self.title


class Section(models.Model):
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.CASCADE, related_name="sections", verbose_name="Чек-лист")
    title = models.CharField("Название блока", max_length=100)
    order = models.PositiveSmallIntegerField("Порядок", default=0)
    per_toilet = models.BooleanField(
        "Для каждого санузла", default=False,
        help_text="Пункты повторяются столько раз, сколько санузлов у точки",
    )
    includes_staff = models.BooleanField(
        "Список персонала", default=False,
        help_text="Перед пунктами показать сотрудников точки: приход и униформа",
    )

    class Meta:
        verbose_name = "Блок"
        verbose_name_plural = "Блоки"
        ordering = ["order"]

    def __str__(self) -> str:
        return self.title


class Item(models.Model):
    class PhotoRule(models.TextChoices):
        NEVER = "never", "Не нужно"
        ON_PROBLEM = "on_problem", "Только при отклонении"
        RANDOM = "random", "Случайная проверка"
        ALWAYS = "always", "Всегда"

    section = models.ForeignKey(Section, on_delete=models.CASCADE, related_name="items", verbose_name="Блок")
    title = models.CharField("Пункт", max_length=150)
    hint = models.TextField("Подсказка: что смотреть", blank=True)
    order = models.PositiveSmallIntegerField("Порядок", default=0)
    photo_rule = models.CharField("Фото", max_length=20, choices=PhotoRule.choices, default=PhotoRule.ON_PROBLEM)
    problems = models.TextField("Типовые проблемы", blank=True, help_text="По одной в строке")
    critical_problems = models.TextField(
        "Критичные проблемы", blank=True,
        help_text="По одной в строке. Если выбрана такая проблема, статус сразу «Критично»",
    )
    is_active = models.BooleanField("Используется", default=True)

    class Meta:
        verbose_name = "Пункт"
        verbose_name_plural = "Пункты"
        ordering = ["order"]

    def __str__(self) -> str:
        return self.title

    @staticmethod
    def _lines(text: str) -> list[str]:
        return [line.strip() for line in text.splitlines() if line.strip()]

    @property
    def problem_list(self) -> list[str]:
        return self._lines(self.problems)

    @property
    def critical_list(self) -> list[str]:
        return self._lines(self.critical_problems)


class Status(models.TextChoices):
    OK = "ok", "Норма"
    DEVIATION = "deviation", "Отклонение"
    CRITICAL = "critical", "Критично"


class Run(models.Model):
    """Прохождение одного чек-листа в одной смене."""

    shift = models.ForeignKey("shifts.Shift", on_delete=models.CASCADE, related_name="checklist_runs", verbose_name="Смена")
    template = models.ForeignKey(ChecklistTemplate, on_delete=models.PROTECT, verbose_name="Чек-лист")
    started_at = models.DateTimeField("Начало", auto_now_add=True)
    finished_at = models.DateTimeField("Завершено", null=True, blank=True)
    random_photo_keys = models.JSONField("Пункты со случайным фото", default=list, blank=True)
    too_fast = models.BooleanField("Слишком быстро", default=False)

    class Meta:
        verbose_name = "Проверка"
        verbose_name_plural = "Проверки"
        ordering = ["-started_at"]
        constraints = [models.UniqueConstraint(fields=["shift", "template"], name="one_run_per_shift_template")]

    def __str__(self) -> str:
        return f"{self.template} · {self.shift}"


class ItemResult(models.Model):
    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="results", verbose_name="Проверка")
    item = models.ForeignKey(Item, on_delete=models.SET_NULL, null=True, verbose_name="Пункт")
    toilet_no = models.PositiveSmallIntegerField("Санузел №", default=0, help_text="0 — пункт не про санузел")
    label = models.CharField("Пункт (копия)", max_length=250)
    status = models.CharField("Статус", max_length=20, choices=Status.choices)
    problem = models.CharField("Проблема", max_length=200, blank=True)
    comment = models.TextField("Комментарий", blank=True)
    photo = models.ImageField("Фото", upload_to="checks/%Y/%m/%d/", blank=True)
    answered_at = models.DateTimeField("Время ответа", auto_now=True)

    class Meta:
        verbose_name = "Ответ"
        verbose_name_plural = "Ответы"
        constraints = [models.UniqueConstraint(fields=["run", "item", "toilet_no"], name="one_answer_per_item")]

    def __str__(self) -> str:
        return f"{self.label}: {self.get_status_display()}"


class StaffCheck(models.Model):
    """Приход и униформа одного сотрудника в этой смене."""

    class Attendance(models.TextChoices):
        PRESENT = "present", "Пришёл"
        LATE = "late", "Опоздал"
        ABSENT = "absent", "Не вышел"

    UNIFORM_ITEMS = [
        "Кепка", "Сетка для волос", "Футболка или китель", "Фартук",
        "Чёрные брюки", "Чёрные кроксы", "Бейдж", "Форма грязная или повреждена",
    ]

    run = models.ForeignKey(Run, on_delete=models.CASCADE, related_name="staff_checks", verbose_name="Проверка")
    employee = models.ForeignKey("staff.Employee", on_delete=models.PROTECT, related_name="checks", verbose_name="Сотрудник")
    attendance = models.CharField("Приход", max_length=20, choices=Attendance.choices)
    uniform_ok = models.BooleanField("Форма в порядке", null=True, blank=True)
    uniform_missing = models.CharField("Нарушения формы", max_length=300, blank=True)
    not_admitted = models.BooleanField("Не допущен к работе", default=False)
    comment = models.TextField("Комментарий", blank=True)
    photo = models.ImageField("Фото", upload_to="staff/%Y/%m/%d/", blank=True)
    answered_at = models.DateTimeField("Время ответа", auto_now=True)

    class Meta:
        verbose_name = "Проверка сотрудника"
        verbose_name_plural = "Проверки сотрудников"
        constraints = [models.UniqueConstraint(fields=["run", "employee"], name="one_check_per_employee")]

    def __str__(self) -> str:
        return f"{self.employee}: {self.get_attendance_display()}"


class Task(models.Model):
    """Задача из отклонения. Закрывается только перепроверкой с фото «после»."""

    class TaskStatus(models.TextChoices):
        OPEN = "open", "Открыта"
        DONE = "done", "Закрыта"

    point = models.ForeignKey("points.Point", on_delete=models.PROTECT, related_name="tasks", verbose_name="Точка")
    shift = models.ForeignKey("shifts.Shift", on_delete=models.PROTECT, related_name="tasks", verbose_name="Смена, где создана")
    result = models.OneToOneField(ItemResult, on_delete=models.SET_NULL, null=True, blank=True, related_name="task")
    title = models.CharField("Что не так", max_length=300)
    is_critical = models.BooleanField("Критично", default=False)
    responsible = models.ForeignKey(
        "staff.Employee", on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Ответственный",
    )
    deadline = models.DateTimeField("Срок")
    status = models.CharField("Статус", max_length=10, choices=TaskStatus.choices, default=TaskStatus.OPEN)
    photo_after = models.ImageField("Фото «после»", upload_to="tasks/%Y/%m/%d/", blank=True)
    created_at = models.DateTimeField("Создана", auto_now_add=True)
    done_at = models.DateTimeField("Закрыта", null=True, blank=True)
    done_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Перепроверил",
    )
    overdue_notified = models.BooleanField("Управляющему сообщено о просрочке", default=False)

    class Meta:
        verbose_name = "Задача"
        verbose_name_plural = "Задачи"
        ordering = ["status", "deadline"]

    def __str__(self) -> str:
        return self.title
