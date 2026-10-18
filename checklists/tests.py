"""Автотесты чек-листов и задач. Запуск: python manage.py test"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from points.models import Point
from shifts.models import Shift
from shifts.tests import fake_selfie
from staff.models import Employee

from .models import ChecklistTemplate, Item, ItemResult, Run, StaffCheck, Status, Task
from .services import (
    ChecklistError, answer_item, answer_staff, build_view, close_task, finish_run, get_or_start_run,
    photo_required, progress,
)


def make_shift(point, admin):
    now = timezone.now()
    return Shift.objects.create(
        point=point, admin=admin, kind=Shift.Kind.DAY,
        planned_start=now - timedelta(hours=1), planned_end=now + timedelta(hours=11),
        checked_in_at=now - timedelta(hours=1), latitude=43.2, longitude=76.9,
        accuracy_m=20, distance_m=10, selfie=fake_selfie(),
    )


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", CHECKLIST_RANDOM_PHOTOS=2)
class ChecklistServiceTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27", latitude=43.2, longitude=76.9, toilets_count=2)
        self.admin = User.objects.create_user("87010000001", password="x", first_name="Ислам")
        self.admin.points.add(self.point)
        self.cashier = Employee.objects.create(point=self.point, full_name="Айдос", position="cashier")
        self.cleaner = Employee.objects.create(point=self.point, full_name="Гульнар", position="cleaner")
        self.shift = make_shift(self.point, self.admin)
        self.template = ChecklistTemplate.objects.get(code="hall")
        self.run = get_or_start_run(self.shift, self.template)

    def item(self, title, section="Зал"):
        return Item.objects.get(section__template=self.template, section__title=section, title=title)

    def test_seeded_checklist_counts(self):
        # 14 пунктов зала + 3 пункта × 2 санузла + 1 общее фото + 2 сотрудника
        self.assertEqual(progress(self.run), (0, 14 + 6 + 1 + 2))

    def test_toilets_are_numbered_when_there_are_several(self):
        labels = [e.label for v in build_view(self.run) for e in v.entries]
        self.assertIn("Санузел 1: Чистота", labels)
        self.assertIn("Санузел 2: Расходники", labels)

    def test_random_photos_are_picked_once(self):
        self.assertEqual(len(self.run.random_photo_keys), 2)
        again = get_or_start_run(self.shift, self.template)
        self.assertEqual(again.random_photo_keys, self.run.random_photo_keys)

    def test_photo_rules(self):
        clean = self.item("Чистота зала")
        toilet = self.item("Чистота", "Санузлы")
        self.assertFalse(photo_required(self.run, clean, 0, Status.OK))
        self.assertTrue(photo_required(self.run, clean, 0, Status.DEVIATION))
        self.assertTrue(photo_required(self.run, toilet, 1, Status.OK))

    def test_ok_answer(self):
        result = answer_item(self.run, self.item("Чистота зала"), 0, status=Status.OK, user=self.admin)
        self.assertEqual(result.status, Status.OK)
        self.assertFalse(Task.objects.exists())

    def test_toilet_needs_photo_even_when_ok(self):
        with self.assertRaisesMessage(ChecklistError, "нужно фото"):
            answer_item(self.run, self.item("Чистота", "Санузлы"), 1, status=Status.OK, user=self.admin)

    def test_deviation_creates_task_with_deadline(self):
        now = timezone.now()
        answer_item(
            self.run, self.item("Чистота зала"), 0, status=Status.DEVIATION, problem="Грязный пол",
            photo=fake_selfie(), responsible=self.cleaner, deadline_minutes=15, user=self.admin, now=now,
        )
        task = Task.objects.get()
        self.assertEqual(task.title, "Чистота зала — Грязный пол")
        self.assertEqual(task.responsible, self.cleaner)
        self.assertEqual(task.deadline, now + timedelta(minutes=15))
        self.assertFalse(task.is_critical)

    def test_deviation_needs_description(self):
        with self.assertRaisesMessage(ChecklistError, "Опишите"):
            answer_item(self.run, self.item("Чистота зала"), 0, status=Status.DEVIATION, photo=fake_selfie(), user=self.admin)

    def test_critical_problem_sets_critical_and_notifies(self):
        with mock.patch("checklists.services.notify") as notify:
            result = answer_item(
                self.run, self.item("Расходники", "Санузлы"), 2, status=Status.DEVIATION, problem="Нет мыла",
                photo=fake_selfie(), user=self.admin,
            )
        self.assertEqual(result.status, Status.CRITICAL)
        self.assertTrue(Task.objects.get().is_critical)
        self.assertIn("Санузел 2: Расходники — Нет мыла", notify.call_args.args[3])

    def test_answer_with_task_cannot_be_changed(self):
        item = self.item("Чистота зала")
        answer_item(self.run, item, 0, status=Status.DEVIATION, problem="Грязный пол", photo=fake_selfie(), user=self.admin)
        with self.assertRaisesMessage(ChecklistError, "уже создана задача"):
            answer_item(self.run, item, 0, status=Status.OK, user=self.admin)

    def test_staff_uniform_violation_needs_photo_and_details(self):
        with self.assertRaisesMessage(ChecklistError, "чего не хватает"):
            answer_staff(self.run, self.cashier, attendance="present", uniform_ok=False, user=self.admin)
        with self.assertRaisesMessage(ChecklistError, "Сфотографируйте"):
            answer_staff(self.run, self.cashier, attendance="present", uniform_ok=False, missing=["Фартук"], user=self.admin)
        check = answer_staff(
            self.run, self.cashier, attendance="present", uniform_ok=False, missing=["Фартук", "Бейдж"],
            comment="Выдан запасной фартук", photo=fake_selfie(), user=self.admin,
        )
        self.assertEqual(check.uniform_missing, "Фартук, Бейдж")

    def test_absent_employee_notifies_manager(self):
        with mock.patch("checklists.services.notify") as notify:
            check = answer_staff(self.run, self.cleaner, attendance="absent", user=self.admin)
        self.assertIsNone(check.uniform_ok)
        self.assertIn("не вышел", notify.call_args.args[2])

    def test_not_admitted_is_critical(self):
        with mock.patch("checklists.services.notify") as notify:
            answer_staff(
                self.run, self.cashier, attendance="present", uniform_ok=True, not_admitted=True,
                comment="Температура, кашель", photo=fake_selfie(), user=self.admin,
            )
        self.assertIn("КРИТИЧНО", notify.call_args.args[2])

    def complete_everything(self):
        for view in build_view(self.run):
            for entry in view.entries:
                answer_item(self.run, entry.item, entry.toilet_no, status=Status.OK, photo=fake_selfie(), user=self.admin)
            for employee, _ in view.staff:
                answer_staff(self.run, employee, attendance="present", uniform_ok=True, user=self.admin)

    def test_finish_requires_all_answers(self):
        with self.assertRaisesMessage(ChecklistError, "Пройдите все пункты"):
            finish_run(self.run)
        self.complete_everything()
        finish_run(self.run, now=self.run.started_at + timedelta(minutes=12))
        self.assertIsNotNone(self.run.finished_at)
        self.assertFalse(self.run.too_fast)

    def test_too_fast_is_flagged(self):
        self.complete_everything()
        finish_run(self.run, now=self.run.started_at + timedelta(seconds=30))
        self.assertTrue(self.run.too_fast)

    def complete_the_rest(self):
        for view in build_view(self.run):
            for entry in view.entries:
                if not entry.result:
                    answer_item(self.run, entry.item, entry.toilet_no, status=Status.OK, photo=fake_selfie(), user=self.admin)
            for employee, check in view.staff:
                if not check:
                    answer_staff(self.run, employee, attendance="present", uniform_ok=True, user=self.admin)

    def test_finish_sends_one_summary_with_all_problems(self):
        from notifications.models import Notification
        boss = User.objects.create_user("87010000005", role=User.Role.OWNER, telegram_chat_id="555")
        answer_item(self.run, self.item("Освещение"), 0, status=Status.DEVIATION, problem="Не горит лампа",
                    photo=fake_selfie(), responsible=self.cleaner, deadline_minutes=30, user=self.admin)
        answer_staff(self.run, self.cleaner, attendance="absent", user=self.admin)
        answer_staff(self.run, self.cashier, attendance="late", uniform_ok=False, missing=["Фартук"],
                     photo=fake_selfie(), user=self.admin)
        self.complete_the_rest()
        finish_run(self.run, now=self.run.started_at + timedelta(minutes=12))

        summary = Notification.objects.get(event=Notification.Event.PROBLEMS)
        self.assertEqual((summary.recipient, summary.title), (boss, "Неполадки: Осмотр зала"))
        self.assertIn("Неполадок: 4.", summary.text)
        self.assertIn("• Освещение — Не горит лампа (устраняет Гульнар, до ", summary.text)
        self.assertIn("• Гульнар (", summary.text)
        self.assertIn(") не вышел на смену", summary.text)
        self.assertIn(") опоздал", summary.text)
        self.assertIn(": нарушение формы — Фартук", summary.text)
        self.assertNotIn("быстро", summary.text)

    def test_no_summary_when_everything_is_fine(self):
        from notifications.models import Notification
        User.objects.create_user("87010000005", role=User.Role.OWNER, telegram_chat_id="555")
        self.complete_everything()
        finish_run(self.run, now=self.run.started_at + timedelta(minutes=12))
        self.assertFalse(Notification.objects.filter(event=Notification.Event.PROBLEMS).exists())

    def test_too_fast_is_reported_even_without_problems(self):
        from notifications.models import Notification
        User.objects.create_user("87010000005", role=User.Role.OWNER, telegram_chat_id="555")
        self.complete_everything()
        finish_run(self.run, now=self.run.started_at + timedelta(seconds=30))
        summary = Notification.objects.get(event=Notification.Event.PROBLEMS)
        self.assertEqual(summary.title, "Слишком быстрая проверка: Осмотр зала")
        self.assertIn("подозрительно быстро", summary.text)

    def test_finished_run_is_locked(self):
        self.complete_everything()
        finish_run(self.run)
        with self.assertRaisesMessage(ChecklistError, "завершена"):
            answer_item(self.run, self.item("Чистота зала"), 0, status=Status.OK, user=self.admin)

    def test_task_closes_only_with_photo(self):
        answer_item(self.run, self.item("Освещение"), 0, status=Status.DEVIATION, problem="Не горит лампа",
                    photo=fake_selfie(), user=self.admin)
        task = Task.objects.get()
        with self.assertRaisesMessage(ChecklistError, "без фото"):
            close_task(task, self.admin, None)
        close_task(task, self.admin, fake_selfie())
        self.assertEqual(task.status, Task.TaskStatus.DONE)
        self.assertEqual(task.done_by, self.admin)


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media")
class ChecklistViewTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27", latitude=43.2, longitude=76.9)
        self.admin = User.objects.create_user("87010000001", password="test12345", first_name="Ислам")
        self.admin.points.add(self.point)
        self.other = User.objects.create_user("87010000009", password="test12345")
        self.other.points.add(self.point)
        Employee.objects.create(point=self.point, full_name="Айдос", position="cashier")
        self.shift = make_shift(self.point, self.admin)
        self.client.login(username="87010000001", password="test12345")
        self.url = reverse("checklist", args=[self.shift.id, "hall"])

    def test_home_shows_tab(self):
        page = self.client.get(reverse("home"))
        self.assertContains(page, "Осмотр зала")
        self.assertContains(page, "Начать")

    def test_run_page_renders_sections(self):
        page = self.client.get(self.url)
        for text in ("Зал", "Санузлы", "Персонал и униформа", "Айдос", "Общий вид зала", "Нужно фото"):
            self.assertContains(page, text)

    def test_answer_endpoint(self):
        self.client.get(self.url)
        run = Run.objects.get()
        item = Item.objects.get(title="Чистота зала")
        url = reverse("answer_item", args=[run.id, item.id, 0])
        response = self.client.post(url, {"status": "ok"})
        self.assertEqual(response.json()["ok"], True)
        response = self.client.post(reverse("answer_item", args=[run.id, Item.objects.get(title="Общий вид зала").id, 0]),
                                    {"status": "ok"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("нужно фото", response.json()["message"])

    def test_other_admin_cannot_answer(self):
        self.client.get(self.url)
        run = Run.objects.get()
        self.client.login(username="87010000009", password="test12345")
        item = Item.objects.get(title="Чистота зала")
        response = self.client.post(reverse("answer_item", args=[run.id, item.id, 0]), {"status": "ok"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ItemResult.objects.exists())

    def test_finish_incomplete_shows_message(self):
        self.client.get(self.url)
        run = Run.objects.get()
        response = self.client.post(reverse("finish_run", args=[run.id]), follow=True)
        self.assertContains(response, "Пройдите все пункты")

    def test_staff_endpoint(self):
        self.client.get(self.url)
        run = Run.objects.get()
        employee = Employee.objects.get()
        response = self.client.post(reverse("answer_staff", args=[run.id, employee.id]),
                                    {"attendance": "present", "uniform": "ok"})
        self.assertTrue(response.json()["ok"])
        self.assertTrue(StaffCheck.objects.get().uniform_ok)
