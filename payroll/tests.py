"""Автотесты зарплатного учёта: отметка сотрудников, штрафы, бонусы, выгрузка в таблицу."""

from datetime import timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from notifications.models import Notification
from points.models import Point
from shifts.models import Shift
from shifts.services import perform_checkin
from shifts.tests import POINT_LAT, POINT_LON, at, fake_selfie
from staff.models import Employee

from . import sheets
from .models import Attendance, Bonus, FineRules
from .services import PayrollError, add_bonus, mark, marked_count, record_admin


def day_shift(point, admin, day=16):
    return Shift.objects.create(
        point=point, admin=admin, kind=Shift.Kind.DAY, planned_start=at(10, day=day), planned_end=at(22, day=day),
        checked_in_at=at(9, 55, day=day), selfie=fake_selfie(),
    )


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", GOOGLE_SHEET_URL="")
class FineRulesTests(TestCase):
    def test_default_tiers(self):
        rules = FineRules.current()
        self.assertEqual([rules.late_fine(m) for m in (0, 1, 14, 15, 59, 60, 300)], [0, 1000, 1000, 3000, 3000, 5000, 5000])
        self.assertEqual(rules.absence_fine, 0)


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", GOOGLE_SHEET_URL="")
class MarkTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27")
        self.admin = User.objects.create_user("87010000001", first_name="Ислам")
        self.admin.points.add(self.point)
        self.boss = User.objects.create_user("87010000009", role=User.Role.OWNER, telegram_chat_id="999")
        self.cashier = Employee.objects.create(point=self.point, full_name="Айдос", position="cashier")
        self.shift = day_shift(self.point, self.admin)

    def test_on_time(self):
        a = mark(self.shift, self.cashier, "on_time", user=self.admin, now=at(10, 30))
        self.assertEqual((a.status, a.fine, a.late_minutes), ("on_time", 0, 0))
        self.assertEqual(marked_count(self.shift), (1, 1))

    def test_late_counts_minutes_and_fine(self):
        a = mark(self.shift, self.cashier, "late", arrived="10:25", reason="Пробка", user=self.admin, now=at(10, 30))
        self.assertEqual((a.late_minutes, a.fine), (25, 3000))
        self.assertEqual(a.arrived_at, at(10, 25))

    def test_late_needs_reason_and_real_lateness(self):
        with self.assertRaisesMessage(PayrollError, "причину опоздания"):
            mark(self.shift, self.cashier, "late", arrived="10:25", user=self.admin, now=at(10, 30))
        with self.assertRaisesMessage(PayrollError, "опоздания нет"):
            mark(self.shift, self.cashier, "late", arrived="09:58", reason="x", user=self.admin, now=at(10, 30))
        with self.assertRaisesMessage(PayrollError, "позже, чем сейчас"):
            mark(self.shift, self.cashier, "late", arrived="11:00", reason="x", user=self.admin, now=at(10, 30))
        with self.assertRaisesMessage(PayrollError, "во сколько"):
            mark(self.shift, self.cashier, "late", arrived="", reason="x", user=self.admin, now=at(10, 30))

    def test_point_grace_minutes(self):
        self.point.late_grace_minutes = 5
        self.point.save()
        with self.assertRaisesMessage(PayrollError, "опоздания нет"):
            mark(self.shift, self.cashier, "late", arrived="10:04", reason="x", user=self.admin, now=at(10, 30))

    def test_night_shift_after_midnight(self):
        night = Shift.objects.create(
            point=self.point, admin=self.admin, kind=Shift.Kind.NIGHT, planned_start=at(22), planned_end=at(10, day=17),
            checked_in_at=at(21, 50), selfie=fake_selfie(),
        )
        a = mark(night, self.cashier, "late", arrived="00:10", reason="Автобус", user=self.admin, now=at(0, 30, day=17))
        self.assertEqual((a.late_minutes, a.fine), (130, 5000))

    def test_absent_needs_reason_notifies_and_uses_rule(self):
        FineRules.objects.filter(pk=FineRules.current().pk).update(absence_fine=10000)
        with self.assertRaisesMessage(PayrollError, "почему сотрудник не вышел"):
            mark(self.shift, self.cashier, "absent", user=self.admin)
        a = mark(self.shift, self.cashier, "absent", reason="Заболел", user=self.admin)
        self.assertEqual(a.fine, 10000)
        alert = Notification.objects.get(event=Notification.Event.URGENT)
        self.assertEqual(alert.title, "Сотрудник не вышел на смену")
        self.assertIn("Причина: Заболел", alert.text)
        mark(self.shift, self.cashier, "absent", reason="Заболел, справка", user=self.admin)
        self.assertEqual(Notification.objects.filter(event=Notification.Event.URGENT).count(), 1)  # не дублируем

    def test_remark_recalculates_and_resets_manual_fine(self):
        a = mark(self.shift, self.cashier, "late", arrived="10:20", reason="x", user=self.admin, now=at(10, 30))
        Attendance.objects.filter(pk=a.pk).update(fine=0, fine_manual=True, fine_note="Уважительная")
        a = mark(self.shift, self.cashier, "on_time", user=self.admin, now=at(10, 30))
        self.assertEqual((a.status, a.fine, a.fine_manual, a.fine_note, a.reason), ("on_time", 0, False, "", ""))

    def test_admin_is_not_in_the_list(self):
        record_admin(self.shift)
        admin_employee = Employee.objects.get(user=self.admin)
        with self.assertRaisesMessage(PayrollError, "нет в списке"):
            mark(self.shift, admin_employee, "on_time", user=self.admin)
        self.assertEqual(marked_count(self.shift), (0, 1))

    def test_bonus(self):
        a = mark(self.shift, self.cashier, "on_time", user=self.admin)
        with self.assertRaisesMessage(PayrollError, "больше нуля"):
            add_bonus(a, amount="0", reason="x", user=self.admin)
        with self.assertRaisesMessage(PayrollError, "целое число"):
            add_bonus(a, amount="много", reason="x", user=self.admin)
        with self.assertRaisesMessage(PayrollError, "за что"):
            add_bonus(a, amount="2000", reason=" ", user=self.admin)
        add_bonus(a, amount="2 000", reason="Вышел на замену", user=self.admin)
        add_bonus(a, amount=500, reason="Чистота", user=self.admin)
        self.assertEqual(a.bonus_total, 2500)
        with self.assertRaisesMessage(PayrollError, "Сначала удалите бонус"):
            mark(self.shift, self.cashier, "absent", reason="x", user=self.admin)

    def test_no_bonus_for_absent(self):
        a = mark(self.shift, self.cashier, "absent", reason="Заболел", user=self.admin)
        with self.assertRaisesMessage(PayrollError, "не работает в эту смену"):
            add_bonus(a, amount=1000, reason="x", user=self.admin)


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", GOOGLE_SHEET_URL="")
class AdminAttendanceTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27", latitude=POINT_LAT, longitude=POINT_LON)
        self.admin = User.objects.create_user("87010000001", password="test12345", first_name="Ислам")
        self.admin.points.add(self.point)

    def test_checkin_records_admin_lateness_and_fine(self):
        shift = perform_checkin(self.admin, None, None, None, fake_selfie(), now=at(10, 20)).shift
        a = Attendance.objects.get(shift=shift)
        self.assertEqual(a.employee.user, self.admin)
        self.assertEqual(a.employee.position, Employee.Position.ADMIN)
        self.assertEqual((a.status, a.late_minutes, a.fine), ("late", 20, 3000))

        self.client.login(username="87010000001", password="test12345")
        self.client.post(reverse("late_reason", args=[shift.id]), {"reason": "Пробка на Абая"})
        a.refresh_from_db()
        self.assertEqual(a.reason, "Пробка на Абая")

    def test_admins_can_be_excused_from_fines(self):
        FineRules.objects.filter(pk=FineRules.current().pk).update(fine_admins=False)
        shift = perform_checkin(self.admin, None, None, None, fake_selfie(), now=at(10, 20)).shift
        self.assertEqual(Attendance.objects.get(shift=shift).fine, 0)

    def test_on_time_admin(self):
        shift = perform_checkin(self.admin, None, None, None, fake_selfie(), now=at(9, 50)).shift
        a = Attendance.objects.get(shift=shift)
        self.assertEqual((a.status, a.fine), ("on_time", 0))


def sheet_response(ok=True, error=""):
    response = mock.Mock(ok=True, status_code=200)
    response.json.return_value = {"ok": ok, "error": error}
    return response


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", GOOGLE_SHEET_URL="https://script.google.com/macros/s/x/exec",
                   GOOGLE_SHEET_SECRET="tajna")
class SheetTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27")
        self.admin = User.objects.create_user("87010000001", first_name="Ислам")
        self.cashier = Employee.objects.create(point=self.point, full_name="Айдос", position="cashier")
        self.shift = day_shift(self.point, self.admin)

    def test_each_change_is_sent_as_one_row(self):
        with mock.patch("payroll.sheets.requests.post", return_value=sheet_response()) as post:
            a = mark(self.shift, self.cashier, "late", arrived="10:25", reason="Пробка", user=self.admin, now=at(10, 30))
            add_bonus(a, amount=2000, reason="Замена", user=self.admin)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["secret"], "tajna")
        self.assertEqual(payload["headers"][:4], ["Ключ", "Дата", "Филиал", "Смена"])
        row = dict(zip(payload["headers"], payload["rows"][0]))
        self.assertEqual(row["Ключ"], f"S{self.shift.id}-E{self.cashier.id}")
        self.assertEqual(row["Дата"], "2026-10-16")
        self.assertEqual((row["Сотрудник"], row["Выход"], row["Пришёл в"]), ("Айдос", "Опоздал", "10:25"))
        self.assertEqual((row["Опоздание, мин"], row["Штраф, ₸"], row["Бонус, ₸"], row["За что бонус"]),
                         (25, 3000, 2000, "Замена"))
        a.refresh_from_db()
        self.assertFalse(a.needs_sync)
        self.assertIsNotNone(a.synced_at)

    def test_error_is_kept_and_retried(self):
        from .sheets import push_pending
        with mock.patch("payroll.sheets.requests.post", return_value=sheet_response(False, "Неверное секретное слово")):
            a = mark(self.shift, self.cashier, "on_time", user=self.admin)
        a.refresh_from_db()
        self.assertTrue(a.needs_sync)
        self.assertEqual(a.sync_error, "Неверное секретное слово")
        with mock.patch("payroll.sheets.requests.post", return_value=sheet_response()):
            self.assertEqual(push_pending(), (1, ""))
        a.refresh_from_db()
        self.assertEqual((a.needs_sync, a.sync_error), (False, ""))

    @override_settings(GOOGLE_SHEET_URL="")
    def test_without_url_rows_wait(self):
        with mock.patch("payroll.sheets.requests.post") as post:
            a = mark(self.shift, self.cashier, "on_time", user=self.admin)
        post.assert_not_called()
        a.refresh_from_db()
        self.assertTrue(a.needs_sync)

    def test_headers_are_unique(self):
        self.assertEqual(len(sheets.HEADERS), len(set(sheets.HEADERS)))


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", GOOGLE_SHEET_URL="")
class AttendancePageTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27")
        self.admin = User.objects.create_user("87010000001", password="test12345", first_name="Ислам")
        self.admin.points.add(self.point)
        self.cashier = Employee.objects.create(point=self.point, full_name="Айдос", position="cashier")
        now = timezone.now()
        self.shift = Shift.objects.create(
            point=self.point, admin=self.admin, kind=Shift.Kind.DAY, planned_start=now - timedelta(hours=1),
            planned_end=now + timedelta(hours=11), checked_in_at=now - timedelta(hours=1), selfie=fake_selfie(),
        )
        record_admin(self.shift)
        self.client.login(username="87010000001", password="test12345")
        self.url = reverse("attendance", args=[self.shift.id])

    def test_home_shows_step(self):
        page = self.client.get(reverse("home"))
        self.assertContains(page, "1. Сотрудники на смене")
        self.assertContains(page, "2. Осмотр зала")

    def test_page_lists_employees_without_admin(self):
        page = self.client.get(self.url)
        self.assertContains(page, "Айдос")
        self.assertContains(page, "отмечено 0 из 1")
        self.assertNotContains(page, "item-title\">Ислам")

    def test_mark_and_bonus(self):
        start = timezone.localtime(self.shift.planned_start)
        arrived = f"{start + timedelta(minutes=16):%H:%M}"
        response = self.client.post(reverse("attendance_mark", args=[self.shift.id, self.cashier.id]),
                                    {"status": "late", "arrived": arrived, "reason": "Пробка"}, follow=True)
        self.assertContains(response, "штраф 3\u00a0000\u00a0₸")
        self.assertContains(response, "Опоздал на")
        response = self.client.post(reverse("attendance_bonus", args=[self.shift.id, self.cashier.id]),
                                    {"amount": "1500", "reason": "Помог на кухне"}, follow=True)
        self.assertContains(response, "Бонус 1\u00a0500\u00a0₸ — Помог на кухне")
        bonus = Bonus.objects.get()
        self.client.post(reverse("bonus_delete", args=[self.shift.id, bonus.id]))
        self.assertFalse(Bonus.objects.exists())

    def test_error_is_shown(self):
        response = self.client.post(reverse("attendance_mark", args=[self.shift.id, self.cashier.id]),
                                    {"status": "absent", "reason": ""}, follow=True)
        self.assertContains(response, "Напишите, почему сотрудник не вышел")

    def test_other_admin_cannot_mark(self):
        User.objects.create_user("87010000002", password="test12345")
        self.client.login(username="87010000002", password="test12345")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        response = self.client.post(reverse("attendance_mark", args=[self.shift.id, self.cashier.id]), {"status": "on_time"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(Attendance.objects.filter(employee=self.cashier).exists())


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", GOOGLE_SHEET_URL="")
class PanelTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27")
        self.boss = User.objects.create_superuser("87000000000", password="test12345", role=User.Role.OWNER)
        self.admin = User.objects.create_user("87010000001")
        self.cashier = Employee.objects.create(point=self.point, full_name="Айдос", position="cashier")
        self.shift = day_shift(self.point, self.admin)
        self.client.login(username="87000000000", password="test12345")

    def test_boss_changes_fine(self):
        a = mark(self.shift, self.cashier, "late", arrived="10:20", reason="Проспал", user=self.admin, now=at(10, 30))
        url = reverse("admin:payroll_attendance_change", args=[a.id])
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {
            "reason": a.reason, "fine": "0", "fine_note": "Уважительная причина",
            "bonuses-TOTAL_FORMS": "1", "bonuses-INITIAL_FORMS": "0", "bonuses-MIN_NUM_FORMS": "0",
            "bonuses-MAX_NUM_FORMS": "1000", "bonuses-0-amount": "3000", "bonuses-0-reason": "Лучший кассир недели",
        }
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 302, response.content[:3000])
        a.refresh_from_db()
        self.assertEqual((a.fine, a.fine_manual, a.fine_note, a.bonus_total), (0, True, "Уважительная причина", 3000))
        self.assertEqual(Bonus.objects.get().created_by, self.boss)
        self.assertTrue(a.needs_sync)

    def test_pages_open(self):
        mark(self.shift, self.cashier, "on_time", user=self.admin)
        self.assertContains(self.client.get(reverse("admin:payroll_attendance_changelist")), "Айдос")
        rules = self.client.get(reverse("admin:payroll_finerules_changelist"), follow=True)
        self.assertContains(rules, "Штраф за невыход")
        self.assertContains(rules, "5000")


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", GOOGLE_SHEET_URL="")
class FullShiftAndDayOffTests(TestCase):
    """17 мкрн: день 10–18, ночь 18–02, полная смена 10–02."""

    def setUp(self):
        from datetime import time
        self.point = Point.objects.create(
            name="17 мкрн", day_shift_start=time(10), day_shift_end=time(18),
            night_shift_start=time(18), night_shift_end=time(2),
        )
        self.day_admin = User.objects.create_user("87010000001", first_name="Ислам")
        self.night_admin = User.objects.create_user("87010000002", first_name="Нурлан")
        self.doner = Employee.objects.create(point=self.point, full_name="Ерлан", position="doner")
        self.cashier = Employee.objects.create(point=self.point, full_name="Айдос", position="cashier")
        self.day = Shift.objects.create(
            point=self.point, admin=self.day_admin, kind=Shift.Kind.DAY, planned_start=at(10), planned_end=at(18),
            checked_in_at=at(9, 55), selfie=fake_selfie(),
        )
        self.night = Shift.objects.create(
            point=self.point, admin=self.night_admin, kind=Shift.Kind.NIGHT, planned_start=at(18),
            planned_end=at(2, day=17), checked_in_at=at(17, 55), selfie=fake_selfie(),
        )

    def test_full_shift_is_visible_to_night_admin(self):
        a = mark(self.day, self.doner, "on_time", full=True, user=self.day_admin, now=at(10, 5))
        self.assertEqual(a.kind, "full")
        mark(self.day, self.cashier, "on_time", user=self.day_admin, now=at(10, 5))
        self.assertEqual(marked_count(self.night), (1, 2))  # Ерлан уже на полной смене
        with self.assertRaisesMessage(PayrollError, "полной смене"):
            mark(self.night, self.doner, "on_time", user=self.night_admin, now=at(18, 5))
        b = mark(self.night, self.cashier, "off", user=self.night_admin, now=at(18, 5))
        self.assertEqual((b.kind, b.fine), ("night", 0))
        self.assertEqual(marked_count(self.night), (2, 2))

    def test_night_admin_cannot_mark_full(self):
        a = mark(self.night, self.cashier, "on_time", full=True, user=self.night_admin, now=at(18, 5))
        self.assertEqual(a.kind, "night")

    def test_point_without_night_has_no_full_shift(self):
        from datetime import time
        point = Point.objects.create(name="16 мкрн", day_shift_end=time(23), has_night_shift=False)
        employee = Employee.objects.create(point=point, full_name="Бауыржан", position="cashier")
        shift = Shift.objects.create(point=point, admin=self.day_admin, kind=Shift.Kind.DAY, planned_start=at(10, day=18),
                                     planned_end=at(23, day=18), checked_in_at=at(9, 55, day=18), selfie=fake_selfie())
        self.assertEqual(mark(shift, employee, "on_time", full=True, user=self.day_admin).kind, "day")

    def test_day_off_has_no_bonus_and_no_fine(self):
        a = mark(self.day, self.cashier, "off", user=self.day_admin)
        self.assertEqual((a.fine, a.reason), (0, ""))
        with self.assertRaisesMessage(PayrollError, "не работает в эту смену"):
            add_bonus(a, amount=1000, reason="x", user=self.day_admin)

    @override_settings(GOOGLE_SHEET_URL="https://script.google.com/macros/s/x/exec")
    def test_day_off_removes_row_from_sheet(self):
        with mock.patch("payroll.sheets.requests.post", return_value=sheet_response()) as post:
            mark(self.day, self.doner, "on_time", full=True, user=self.day_admin, now=at(10, 5))
            first = post.call_args.kwargs["json"]
            mark(self.day, self.doner, "off", user=self.day_admin, now=at(10, 6))
            second = post.call_args.kwargs["json"]
        row = dict(zip(first["headers"], first["rows"][0]))
        self.assertEqual((row["Филиал"], row["Смена"], row["Должность"]), ("17 мкрн", "Полная", "Донерщик"))
        self.assertEqual((second["rows"], second["delete"]), ([], [f"S{self.day.id}-E{self.doner.id}"]))


class BranchesTests(TestCase):
    def test_setup_branches_is_repeatable(self):
        from django.core.management import call_command
        from io import StringIO
        call_command("setup_branches", stdout=StringIO())
        call_command("setup_branches", stdout=StringIO())
        self.assertEqual(Point.objects.filter(name__endswith="мкрн").count(), 4)
        p16 = Point.objects.get(name="16 мкрн")
        self.assertFalse(p16.has_night_shift)
        self.assertEqual(Point.objects.get(name="14 мкрн").schedule_text(), "день 12:00–18:00, ночь 18:00–02:00")

    def test_slots_follow_branch_schedule(self):
        from datetime import time
        from shifts.services import current_slot, full_shift_span
        p17 = Point.objects.create(name="17 мкрн", day_shift_end=time(18), night_shift_start=time(18), night_shift_end=time(2))
        night = current_slot(p17, at(23, 30), early_minutes=60)
        self.assertEqual((night.kind, night.start, night.end), ("night", at(18), at(2, day=17)))
        self.assertIsNone(current_slot(p17, at(4), early_minutes=60))  # с 02:00 до 09:00 смены нет
        self.assertEqual(current_slot(p17, at(9, 30), early_minutes=60).kind, "day")
        self.assertEqual(full_shift_span(p17, at(10)), (at(10), at(2, day=17)))
        p16 = Point.objects.create(name="16 мкрн", day_shift_end=time(23), has_night_shift=False)
        self.assertEqual(current_slot(p16, at(22, 30), early_minutes=60).end, at(23))
        self.assertIsNone(current_slot(p16, at(23, 30), early_minutes=60))


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", GOOGLE_SHEET_URL="https://script.google.com/macros/s/x/exec",
                   GOOGLE_SHEET_SECRET="tajna")
class SheetCheckTests(TestCase):
    def setUp(self):
        User.objects.create_superuser("87000000000", password="test12345", role=User.Role.OWNER)
        self.client.login(username="87000000000", password="test12345")

    def test_check_button(self):
        page = self.client.get(reverse("admin:payroll_attendance_changelist"))
        self.assertContains(page, "Проверить связь с Google Таблицей")
        with mock.patch("payroll.sheets.requests.post", return_value=sheet_response()) as post:
            response = self.client.get(reverse("admin:payroll_check_sheet"), follow=True)
        self.assertContains(response, "Связь с Google Таблицей есть")
        self.assertEqual(post.call_count, 1)
        self.assertEqual((post.call_args.kwargs["json"]["rows"], post.call_args.kwargs["json"]["secret"]), ([], "tajna"))
        with mock.patch("payroll.sheets.requests.post", return_value=sheet_response(False, "Секретное слово не совпадает")):
            response = self.client.get(reverse("admin:payroll_check_sheet"), follow=True)
        self.assertContains(response, "Секретное слово не совпадает")
