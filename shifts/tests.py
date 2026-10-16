"""
Автотесты отметки «Я пришёл». Запуск: python manage.py test
"""

import io
from datetime import datetime, time
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image

from accounts.models import User
from points.models import Point

from .models import CheckinAttempt, Shift
from .services import current_slot, distance_m, format_distance, late_minutes, perform_checkin

ALMATY = ZoneInfo("Asia/Almaty")
# Условная точка и место в 2,4 км от неё
POINT_LAT, POINT_LON = 43.238949, 76.889709
FAR_LAT, FAR_LON = 43.238949, 76.919400


def at(hour, minute=0, day=16):
    return datetime(2026, 10, day, hour, minute, tzinfo=ALMATY)


def fake_selfie(name="selfie.jpg"):
    buffer = io.BytesIO()
    Image.new("RGB", (60, 80), (200, 120, 80)).save(buffer, format="JPEG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/jpeg")


class RulesTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27", latitude=POINT_LAT, longitude=POINT_LON)

    def test_distance_is_correct(self):
        # 1 градус широты ≈ 111,2 км
        self.assertAlmostEqual(distance_m(43.0, 76.0, 44.0, 76.0), 111_195, delta=50)
        self.assertGreater(distance_m(POINT_LAT, POINT_LON, FAR_LAT, FAR_LON), 2_300)

    def test_format_distance(self):
        self.assertEqual(format_distance(347), "350 м")
        self.assertEqual(format_distance(2412), "2,4 км")

    def test_day_shift_window(self):
        slot = current_slot(self.point, at(9, 30), early_minutes=60)
        self.assertEqual(slot.kind, "day")
        self.assertEqual(slot.start, at(10))
        self.assertEqual(slot.end, at(22))

    def test_before_window_still_night(self):
        # В 8:30 на дневную ещё рано — идёт ночная смена, начавшаяся вчера в 22:00
        slot = current_slot(self.point, at(8, 30), early_minutes=60)
        self.assertEqual(slot.kind, "night")
        self.assertEqual(slot.start, at(22, day=15))

    def test_night_shift_after_midnight(self):
        slot = current_slot(self.point, at(1, 15), early_minutes=60)
        self.assertEqual(slot.kind, "night")
        self.assertEqual(slot.start, at(22, day=15))
        self.assertEqual(slot.end, at(10))

    def test_late_minutes(self):
        self.assertEqual(late_minutes(at(10), at(9, 55)), 0)
        self.assertEqual(late_minutes(at(10), at(10, 0)), 0)
        self.assertEqual(late_minutes(at(10), at(10, 15)), 15)
        self.assertEqual(late_minutes(at(10), at(10, 4), grace_minutes=5), 0)
        self.assertEqual(late_minutes(at(10), at(10, 15), grace_minutes=5), 15)


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media")
class CheckinTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27", latitude=POINT_LAT, longitude=POINT_LON, radius_m=100)
        self.admin = User.objects.create_user("87010000001", password="test12345", first_name="Ислам")
        self.admin.points.add(self.point)

    def checkin(self, lat=POINT_LAT, lon=POINT_LON, accuracy=20, now=None):
        return perform_checkin(self.admin, lat, lon, accuracy, fake_selfie(), now=now or at(9, 55))

    def test_on_time_inside_radius(self):
        result = self.checkin()
        self.assertEqual(result.status, "ok")
        self.assertEqual(result.shift.late_minutes, 0)
        self.assertEqual(result.shift.kind, "day")
        self.assertEqual(CheckinAttempt.objects.get().result, "ok")

    def test_late_is_counted_by_server_time(self):
        result = self.checkin(now=at(10, 15))
        self.assertEqual(result.status, "late")
        self.assertEqual(result.shift.late_minutes, 15)
        self.assertTrue(result.shift.needs_late_reason)
        self.assertEqual(result.shift.late_human, "15 мин")

    def test_long_delay_is_shown_in_hours(self):
        result = self.checkin(now=at(13, 7))
        self.assertEqual(result.shift.late_human, "3 ч 7 мин")

    def test_outside_radius_is_rejected_and_logged(self):
        result = self.checkin(lat=FAR_LAT, lon=FAR_LON)
        self.assertEqual(result.status, "outside")
        self.assertIn("2,4 км", result.message)
        self.assertFalse(Shift.objects.exists())
        self.assertEqual(CheckinAttempt.objects.get().result, "outside")

    def test_low_gps_accuracy_is_rejected(self):
        result = self.checkin(accuracy=400)
        self.assertEqual(result.status, "low_accuracy")
        self.assertFalse(Shift.objects.exists())

    def test_second_press_does_not_duplicate(self):
        self.checkin()
        result = self.checkin(now=at(10, 5))
        self.assertEqual(result.status, "already")
        self.assertEqual(Shift.objects.count(), 1)

    def test_nearest_of_several_points_is_used(self):
        far_point = Point.objects.create(name="Астана", latitude=51.128, longitude=71.430)
        self.admin.points.add(far_point)
        result = self.checkin()
        self.assertEqual(result.point, self.point)

    def test_admin_without_points(self):
        self.admin.points.clear()
        self.assertEqual(self.checkin().status, "no_point")


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media")
class ViewsTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27", latitude=POINT_LAT, longitude=POINT_LON)
        self.admin = User.objects.create_user("87010000001", password="test12345", first_name="Ислам")
        self.admin.points.add(self.point)
        self.manager = User.objects.create_user(
            "87010000002", password="test12345", first_name="Нурхат",
            role=User.Role.MANAGER, telegram_chat_id="111",
        )
        self.manager.points.add(self.point)
        self.client.login(username="87010000001", password="test12345")

    def post_checkin(self, **overrides):
        data = {"latitude": POINT_LAT, "longitude": POINT_LON, "accuracy": 25, "selfie": fake_selfie()}
        data.update(overrides)
        return self.client.post(reverse("checkin"), data)

    def test_login_required(self):
        self.client.logout()
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, "/login/?next=/")

    def test_selfie_is_required(self):
        response = self.client.post(reverse("checkin"), {"latitude": POINT_LAT, "longitude": POINT_LON, "accuracy": 25})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["message"], "Без селфи отметиться нельзя.")

    def test_checkin_and_home_page(self):
        with mock.patch("shifts.services.timezone.now", return_value=at(9, 50)):
            response = self.post_checkin()
        self.assertEqual(response.json()["status"], "ok")
        with mock.patch("shifts.views.timezone.now", return_value=at(11)):
            page = self.client.get(reverse("home"))
        self.assertContains(page, "Вы на смене")

    def test_late_reason_notifies_manager(self):
        with mock.patch("shifts.services.timezone.now", return_value=at(10, 15)):
            self.assertEqual(self.post_checkin().json()["status"], "late")
        shift = Shift.objects.get()
        with mock.patch("shifts.views.notify_point_managers") as notify:
            response = self.client.post(reverse("late_reason", args=[shift.id]), {"reason": "Пробка на Абая"})
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        shift.refresh_from_db()
        self.assertEqual(shift.late_reason, "Пробка на Абая")
        text = notify.call_args.args[1]
        self.assertIn("10:15", text)
        self.assertIn("опоздание 15 мин", text)

    def test_manager_cannot_check_in(self):
        self.client.login(username="87010000002", password="test12345")
        self.assertEqual(self.post_checkin().status_code, 403)


class TelegramTests(TestCase):
    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_without_token_message_goes_to_log(self):
        from notifications.telegram import send_message
        with self.assertLogs("otdoner.telegram", level="INFO") as logs:
            self.assertFalse(send_message("111", "проверка"))
        self.assertIn("проверка", logs.output[0])

    @override_settings(TELEGRAM_BOT_TOKEN="test-token")
    def test_with_token_calls_telegram_api(self):
        from notifications.telegram import send_message
        with mock.patch("notifications.telegram.requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            self.assertTrue(send_message("111", "проверка"))
        self.assertIn("/bottest-token/sendMessage", post.call_args.args[0])


@override_settings(MEDIA_ROOT="/tmp/otdoner-test-media", MISSED_CHECKIN_ALERT_MINUTES=10)
class ReminderTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27", latitude=POINT_LAT, longitude=POINT_LON)

    def test_missed_checkin_is_reported_once(self):
        from shifts.management.commands.send_reminders import notify_missed_checkins
        with mock.patch("shifts.management.commands.send_reminders.notify_point_managers") as notify:
            self.assertEqual(notify_missed_checkins(at(10, 5)), 0)   # ещё рано
            self.assertEqual(notify_missed_checkins(at(10, 12)), 1)
            self.assertEqual(notify_missed_checkins(at(10, 20)), 0)  # уже сообщили
        self.assertIn("никто из администраторов не отметился", notify.call_args.args[1])

    def test_no_report_when_admin_checked_in(self):
        from shifts.management.commands.send_reminders import notify_missed_checkins
        admin = User.objects.create_user("87010000001", password="x")
        admin.points.add(self.point)
        perform_checkin(admin, POINT_LAT, POINT_LON, 20, fake_selfie(), now=at(9, 58))
        with mock.patch("shifts.management.commands.send_reminders.notify_point_managers"):
            self.assertEqual(notify_missed_checkins(at(10, 12)), 0)

    def test_overdue_task_is_reported_once(self):
        from checklists.models import Task
        from shifts.management.commands.send_reminders import notify_overdue_tasks
        admin = User.objects.create_user("87010000001", password="x")
        admin.points.add(self.point)
        shift = perform_checkin(admin, POINT_LAT, POINT_LON, 20, fake_selfie(), now=at(9, 58)).shift
        Task.objects.create(point=self.point, shift=shift, title="Освещение — Не горит лампа", deadline=at(10, 30))
        with mock.patch("shifts.management.commands.send_reminders.notify_point_managers") as notify:
            self.assertEqual(notify_overdue_tasks(at(10, 20)), 0)
            self.assertEqual(notify_overdue_tasks(at(10, 31)), 1)
            self.assertEqual(notify_overdue_tasks(at(10, 40)), 0)
        self.assertIn("срок был 10:30", notify.call_args.args[1])
