"""
Правила чек-листов: какие пункты показать, когда нужно фото, как отклонение
превращается в задачу и когда проверку можно завершить.
"""

import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from notifications.telegram import notify_point_managers
from staff.models import Employee

from .models import ChecklistTemplate, Item, ItemResult, Run, Section, StaffCheck, Status, Task

DEADLINE_CHOICES = [(15, "15 минут"), (30, "30 минут"), (60, "1 час"), (0, "До конца смены")]


class ChecklistError(Exception):
    """Понятная администратору ошибка: что исправить, чтобы ответ приняли."""


@dataclass
class Entry:
    """Одна строка чек-листа на экране: пункт (для санузлов — с номером санузла) и ответ."""

    item: Item
    toilet_no: int
    label: str
    result: ItemResult | None = None

    @property
    def key(self) -> str:
        return f"{self.item.id}:{self.toilet_no}"


@dataclass
class SectionView:
    section: Section
    entries: list[Entry] = field(default_factory=list)
    staff: list[tuple[Employee, StaffCheck | None]] = field(default_factory=list)


def make_label(item: Item, toilet_no: int, toilets_count: int) -> str:
    if not toilet_no:
        return item.title
    prefix = f"Санузел {toilet_no}" if toilets_count > 1 else "Санузел"
    return f"{prefix}: {item.title}"


def all_entries(template: ChecklistTemplate, toilets_count: int) -> list[tuple[Section, Entry]]:
    rows = []
    for section in template.sections.prefetch_related("items"):
        items = [i for i in section.items.all() if i.is_active]
        toilet_numbers = range(1, toilets_count + 1) if section.per_toilet else [0]
        for toilet_no in toilet_numbers:
            for item in items:
                rows.append((section, Entry(item, toilet_no, make_label(item, toilet_no, toilets_count))))
    return rows


def get_or_start_run(shift, template: ChecklistTemplate) -> Run:
    run, created = Run.objects.get_or_create(shift=shift, template=template)
    if created:
        # Случайный фотоконтроль: администратор заранее не знает, у каких пунктов попросят фото
        candidates = [
            entry.key for _, entry in all_entries(template, shift.point.toilets_count)
            if entry.item.photo_rule == Item.PhotoRule.RANDOM
        ]
        run.random_photo_keys = random.sample(candidates, min(settings.CHECKLIST_RANDOM_PHOTOS, len(candidates)))
        run.save(update_fields=["random_photo_keys"])
    return run


def staff_on_point(point):
    return Employee.objects.filter(point=point, is_active=True)


def build_view(run: Run) -> list[SectionView]:
    point = run.shift.point
    results = {(r.item_id, r.toilet_no): r for r in run.results.all()}
    checks = {c.employee_id: c for c in run.staff_checks.all()}
    views: dict[int, SectionView] = {}
    for section, entry in all_entries(run.template, point.toilets_count):
        view = views.setdefault(section.id, SectionView(section))
        entry.result = results.get((entry.item.id, entry.toilet_no))
        view.entries.append(entry)
    for section in run.template.sections.all():
        view = views.setdefault(section.id, SectionView(section))
        if section.includes_staff:
            view.staff = [(e, checks.get(e.id)) for e in staff_on_point(point)]
    return [views[s.id] for s in run.template.sections.all() if s.id in views]


def photo_required(run: Run, item: Item, toilet_no: int, status: str) -> bool:
    if item.photo_rule == Item.PhotoRule.ALWAYS:
        return True
    if item.photo_rule == Item.PhotoRule.RANDOM and f"{item.id}:{toilet_no}" in run.random_photo_keys:
        return True
    return status != Status.OK and item.photo_rule != Item.PhotoRule.NEVER


def progress(run: Run) -> tuple[int, int]:
    total_entries = len(all_entries(run.template, run.shift.point.toilets_count))
    has_staff = run.template.sections.filter(includes_staff=True).exists()
    total_staff = staff_on_point(run.shift.point).count() if has_staff else 0
    done = run.results.count() + run.staff_checks.count()
    return done, total_entries + total_staff


def _deadline(run: Run, minutes: int | None, now: datetime) -> datetime:
    if minutes:
        return now + timedelta(minutes=minutes)
    return run.shift.planned_end


def _local(dt: datetime, point) -> str:
    return f"{timezone.localtime(dt, ZoneInfo(point.timezone)):%H:%M}"


def _ensure_open(run: Run):
    if run.finished_at:
        raise ChecklistError("Проверка уже завершена.")


def answer_item(run: Run, item: Item, toilet_no: int, *, status: str, user, problem: str = "", comment: str = "",
                photo=None, responsible: Employee | None = None, deadline_minutes: int | None = None,
                now: datetime | None = None) -> ItemResult:
    now = now or timezone.now()
    _ensure_open(run)
    point = run.shift.point
    if item.section.template_id != run.template_id:
        raise ChecklistError("Этот пункт из другого чек-листа.")
    if toilet_no and (not item.section.per_toilet or toilet_no > point.toilets_count):
        raise ChecklistError("Такого санузла нет.")

    existing = ItemResult.objects.filter(run=run, item=item, toilet_no=toilet_no).first()
    if existing and existing.status != Status.OK:
        raise ChecklistError("По этому пункту уже создана задача. Закройте её перепроверкой.")

    problem = problem.strip()
    comment = comment.strip()
    if problem and problem in item.critical_list:
        status = Status.CRITICAL
    if status not in Status.values:
        raise ChecklistError("Выберите статус.")
    if status != Status.OK and not (problem or comment):
        raise ChecklistError("Опишите, что не так: выберите проблему или напишите комментарий.")
    if photo_required(run, item, toilet_no, status) and not photo:
        raise ChecklistError("Для этого пункта нужно фото.")
    if responsible and responsible.point_id != point.id:
        raise ChecklistError("Ответственный должен работать на этой точке.")

    label = make_label(item, toilet_no, point.toilets_count)
    with transaction.atomic():
        result = existing or ItemResult(run=run, item=item, toilet_no=toilet_no)
        result.label = label
        result.status = status
        result.problem = problem
        result.comment = comment
        if photo:
            result.photo = photo
        result.save()

        if status != Status.OK:
            task = Task.objects.create(
                point=point, shift=run.shift, result=result,
                title=f"{label} — {problem or comment}",
                is_critical=status == Status.CRITICAL,
                responsible=responsible,
                deadline=_deadline(run, deadline_minutes, now),
            )

    if status == Status.CRITICAL:
        who = f"Ответственный: {responsible}, срок {_local(task.deadline, point)}." if responsible else "Ответственный не назначен."
        notify_point_managers(
            point,
            f"КРИТИЧНО · Точка «{point}»\n{task.title}\n"
            f"Администратор: {user}. {who}" + (f"\nКомментарий: {comment}" if comment and problem else ""),
        )
    return result


def answer_staff(run: Run, employee: Employee, *, attendance: str, user, uniform_ok: bool | None = None,
                 missing: list[str] | None = None, not_admitted: bool = False, comment: str = "",
                 photo=None) -> StaffCheck:
    _ensure_open(run)
    point = run.shift.point
    if employee.point_id != point.id:
        raise ChecklistError("Этот сотрудник работает на другой точке.")
    if attendance not in StaffCheck.Attendance.values:
        raise ChecklistError("Отметьте, пришёл ли сотрудник.")

    missing = [m for m in (missing or []) if m in StaffCheck.UNIFORM_ITEMS]
    comment = comment.strip()
    if attendance == StaffCheck.Attendance.ABSENT:
        uniform_ok, missing, not_admitted = None, [], False
    else:
        if uniform_ok is None:
            raise ChecklistError("Отметьте, в порядке ли форма.")
        if uniform_ok is False and not (missing or comment):
            raise ChecklistError("Отметьте, чего не хватает в форме, или напишите комментарий.")
        if (uniform_ok is False or not_admitted) and not photo:
            raise ChecklistError("Сфотографируйте сотрудника.")
        if not_admitted and not comment:
            raise ChecklistError("Напишите, почему сотрудник не допущен к работе.")

    check, _ = StaffCheck.objects.update_or_create(
        run=run, employee=employee,
        defaults={
            "attendance": attendance, "uniform_ok": uniform_ok, "uniform_missing": ", ".join(missing),
            "not_admitted": not_admitted, "comment": comment,
        },
    )
    if photo:
        check.photo = photo
        check.save(update_fields=["photo"])

    if not_admitted:
        notify_point_managers(
            point,
            f"КРИТИЧНО · Точка «{point}»\nСотрудник {employee} ({employee.get_position_display().lower()}) "
            f"не допущен к работе.\nПричина: {comment}\nАдминистратор: {user}.",
        )
    elif attendance == StaffCheck.Attendance.ABSENT:
        notify_point_managers(
            point,
            f"Точка «{point}»: сотрудник {employee} ({employee.get_position_display().lower()}) не вышел на смену.",
        )
    return check


def finish_run(run: Run, now: datetime | None = None) -> Run:
    now = now or timezone.now()
    _ensure_open(run)
    done, total = progress(run)
    if done < total:
        raise ChecklistError(f"Отмечено {done} из {total}. Пройдите все пункты.")
    seconds = (now - run.started_at).total_seconds()
    run.too_fast = total > 0 and seconds < total * settings.CHECKLIST_MIN_SECONDS_PER_ITEM
    run.finished_at = now
    run.save(update_fields=["finished_at", "too_fast"])
    return run


def open_tasks(point):
    return Task.objects.filter(point=point, status=Task.TaskStatus.OPEN).select_related("responsible", "result")


def close_task(task: Task, user, photo, now: datetime | None = None) -> Task:
    if task.status != Task.TaskStatus.OPEN:
        raise ChecklistError("Задача уже закрыта.")
    if not photo:
        raise ChecklistError("Сфотографируйте результат: без фото «после» задачу не закрыть.")
    task.photo_after = photo
    task.status = Task.TaskStatus.DONE
    task.done_at = now or timezone.now()
    task.done_by = user
    task.save()
    return task
