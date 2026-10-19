"""Автотесты уведомлений: кому, куда и что уходит. Запуск: python manage.py test"""

from datetime import timedelta
from unittest import mock

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from points.models import Point

from . import telegram, whatsapp
from .models import Notification
from .service import notify, recipients_for, retry_unsent

Event, State = Notification.Event, Notification.State


def api_response(status=200, payload=None):
    response = mock.Mock(ok=status < 400, status_code=status, text="")
    response.json.return_value = payload or {}
    return response


class RecipientsTests(TestCase):
    def setUp(self):
        self.point = Point.objects.create(name="27")
        self.other = Point.objects.create(name="Абая")
        self.manager = User.objects.create_user("1", role=User.Role.MANAGER, telegram_chat_id="111")
        self.manager.points.add(self.point)
        self.other_manager = User.objects.create_user("2", role=User.Role.MANAGER, telegram_chat_id="222")
        self.other_manager.points.add(self.other)
        self.boss = User.objects.create_user("3", role=User.Role.OWNER, whatsapp_phone="77010000003")
        self.admin = User.objects.create_user("4", telegram_chat_id="444")
        self.admin.points.add(self.point)

    def test_manager_of_point_and_owner_without_points(self):
        self.assertEqual(list(recipients_for(self.point, Event.LATE)), [self.manager, self.boss])
        self.assertEqual(list(recipients_for(self.other, Event.LATE)), [self.other_manager, self.boss])

    def test_owner_with_points_gets_only_them(self):
        self.boss.points.add(self.other)
        self.assertEqual(list(recipients_for(self.point, Event.LATE)), [self.manager])

    def test_event_can_be_switched_off(self):
        self.boss.notify_problems = False
        self.boss.save()
        self.assertEqual(list(recipients_for(self.point, Event.PROBLEMS)), [self.manager])
        self.assertIn(self.boss, recipients_for(self.point, Event.LATE))

    @override_settings(TELEGRAM_BOT_TOKEN="", WHATSAPP_TOKEN="")
    def test_every_messenger_gets_a_copy(self):
        self.manager.whatsapp_phone = "77010000001"
        self.manager.save()
        self.assertEqual(notify(self.point, Event.LATE, "Опоздание администратора", "Точка «27»"), 3)
        channels = sorted(Notification.objects.values_list("recipient__username", "channel"))
        self.assertEqual(channels, [("1", "telegram"), ("1", "whatsapp"), ("3", "whatsapp")])
        # Ключей нет — ничего не отправлено, причина записана
        self.assertEqual(set(Notification.objects.values_list("state", flat=True)), {State.SKIPPED})

    def test_nobody_to_notify_is_logged(self):
        point = Point.objects.create(name="Новая")
        self.boss.points.add(self.other)
        self.assertEqual(notify(point, Event.LATE, "Опоздание", "текст"), 0)
        self.assertIn("Некому отправить", Notification.objects.get().error)


class TelegramTests(TestCase):
    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_without_token_message_goes_to_log(self):
        with self.assertLogs("otdoner.telegram", level="INFO") as logs:
            outcome = telegram.send_message("111", "проверка")
        self.assertTrue(outcome.skipped)
        self.assertIn("проверка", logs.output[0])

    @override_settings(TELEGRAM_BOT_TOKEN="test-token")
    def test_with_token_calls_telegram_api(self):
        with mock.patch("notifications.telegram.requests.post", return_value=api_response()) as post:
            self.assertTrue(telegram.send_message("111", "проверка").ok)
        self.assertIn("/bottest-token/sendMessage", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["json"], {"chat_id": "111", "text": "проверка"})

    @override_settings(TELEGRAM_BOT_TOKEN="test-token")
    def test_error_has_a_hint(self):
        response = api_response(403, {"description": "Forbidden: bot can't initiate conversation with a user"})
        with mock.patch("notifications.telegram.requests.post", return_value=response), self.assertLogs("otdoner"):
            outcome = telegram.send_message("111", "проверка")
        self.assertFalse(outcome.ok)
        self.assertIn("нажать «Старт»", outcome.error)


@override_settings(WHATSAPP_TOKEN="wa-token", WHATSAPP_PHONE_NUMBER_ID="555", WHATSAPP_API_VERSION="v23.0",
                   WHATSAPP_TEMPLATE="otdoner_alert", WHATSAPP_TEMPLATE_LANG="ru")
class WhatsAppTests(TestCase):
    def test_phone_is_normalized(self):
        for raw in ["87011234567", "+7 (701) 123-45-67", "7011234567", "77011234567"]:
            self.assertEqual(whatsapp.normalize_phone(raw), "77011234567")

    def test_user_phone_is_cleaned(self):
        user = User(username="1", whatsapp_phone="8 701 123 45 67")
        user.clean()
        self.assertEqual(user.whatsapp_phone, "77011234567")
        with self.assertRaises(ValidationError):
            User(username="2", whatsapp_phone="12345").clean()

    def test_template_message(self):
        with mock.patch("notifications.whatsapp.requests.post", return_value=api_response()) as post:
            outcome = whatsapp.send_message("87011234567", "Опоздание администратора", "Точка «27»\nПричина:    пробка")
        self.assertTrue(outcome.ok)
        self.assertEqual(post.call_args.args[0], "https://graph.facebook.com/v23.0/555/messages")
        self.assertEqual(post.call_args.kwargs["headers"], {"Authorization": "Bearer wa-token"})
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["to"], "77011234567")
        self.assertEqual(body["template"]["name"], "otdoner_alert")
        self.assertEqual(body["template"]["language"], {"code": "ru"})
        params = [p["text"] for p in body["template"]["components"][0]["parameters"]]
        # В переменных шаблона WhatsApp не принимает переносы строк и много пробелов подряд
        self.assertEqual(params, ["Опоздание администратора", "Точка «27» · Причина: пробка"])

    @override_settings(WHATSAPP_TEMPLATE="")
    def test_plain_text_without_template(self):
        with mock.patch("notifications.whatsapp.requests.post", return_value=api_response()) as post:
            whatsapp.send_message("77011234567", "Проверка", "строка 1\nстрока 2")
        body = post.call_args.kwargs["json"]
        self.assertEqual(body["type"], "text")
        self.assertEqual(body["text"]["body"], "Проверка\nстрока 1\nстрока 2")

    def test_error_has_a_hint_and_is_retried(self):
        point = Point.objects.create(name="27")
        boss = User.objects.create_user("3", role=User.Role.OWNER, whatsapp_phone="77010000003")
        error = api_response(400, {"error": {"code": 131030, "message": "Recipient phone number not in allowed list"}})
        with mock.patch("notifications.whatsapp.requests.post", return_value=error), self.assertLogs("otdoner"):
            notify(point, Event.URGENT, "КРИТИЧНО", "Нет воды")
        message = Notification.objects.get(recipient=boss)
        self.assertEqual(message.state, State.FAILED)
        self.assertIn("не в списке получателей тестового номера", message.error)

        with mock.patch("notifications.whatsapp.requests.post", return_value=api_response()):
            self.assertEqual(retry_unsent(), 1)
        message.refresh_from_db()
        self.assertEqual((message.state, message.attempts, message.error), (State.SENT, 2, ""))
        self.assertEqual(retry_unsent(), 0)

    def test_retry_gives_up_after_three_attempts_and_two_hours(self):
        old = Notification.objects.create(
            event=Event.LATE, channel="whatsapp", address="7701", title="t", text="x", state=State.FAILED,
            attempts=1, created_at=timezone.now() - timedelta(hours=3),
        )
        tired = Notification.objects.create(
            event=Event.LATE, channel="whatsapp", address="7701", title="t", text="x", state=State.FAILED, attempts=3,
        )
        with mock.patch("notifications.whatsapp.requests.post") as post:
            self.assertEqual(retry_unsent(), 0)
        post.assert_not_called()
        self.assertEqual({old.state, tired.state}, {State.FAILED})


class PanelTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_superuser("87000000000", password="test12345", role=User.Role.OWNER,
                                                  telegram_chat_id="111", whatsapp_phone="77000000000")
        self.client.login(username="87000000000", password="test12345")

    @override_settings(TELEGRAM_BOT_TOKEN="test-token", WHATSAPP_TOKEN="", WHATSAPP_PHONE_NUMBER_ID="")
    def test_send_test_message_action(self):
        with mock.patch("notifications.telegram.requests.post", return_value=api_response()), self.assertLogs("otdoner"):
            response = self.client.post(
                reverse("admin:accounts_user_changelist"),
                {"action": "send_test_message", "_selected_action": [self.boss.pk]}, follow=True,
            )
        text = response.content.decode()
        self.assertIn("Telegram: отправлено", text)
        self.assertIn("WhatsApp: не ушло", text)
        self.assertEqual(Notification.objects.filter(event=Event.TEST).count(), 2)

    def test_journal_page_opens(self):
        Notification.objects.create(event=Event.LATE, title="Опоздание администратора", text="Точка «27»",
                                    state=State.SKIPPED, error="Нет ключа")
        response = self.client.get(reverse("admin:notifications_notification_changelist"))
        self.assertContains(response, "Нет ключа")


def telegram_api(username="otdoner_alerts_bot", updates=None, send_ok=True):
    """Поддельный Telegram: отвечает на getMe, getUpdates и sendMessage."""
    def fake_post(url, json=None, timeout=None):
        method = url.rsplit("/", 1)[-1]
        if method == "getMe":
            return api_response(payload={"ok": True, "result": {"username": username}})
        if method == "getUpdates":
            return api_response(payload={"ok": True, "result": updates or []})
        if send_ok:
            return api_response(payload={"ok": True, "result": {}})
        return api_response(403, {"ok": False, "description": "Forbidden: bot was blocked by the user"})
    return mock.patch("notifications.telegram.requests.post", side_effect=fake_post)


@override_settings(TELEGRAM_BOT_TOKEN="test-token", WHATSAPP_TOKEN="", WHATSAPP_PHONE_NUMBER_ID="")
class TelegramConnectTests(TestCase):
    def setUp(self):
        telegram._usernames.clear()
        self.boss = User.objects.create_user("87000000000", password="test12345", first_name="Шеф",
                                             role=User.Role.OWNER)
        self.client.login(username="87000000000", password="test12345")

    def start_update(self, text, chat_id=777, update_id=1):
        return {"update_id": update_id, "message": {"text": text, "chat": {"id": chat_id}}}

    def test_manager_home_shows_connect_card(self):
        page = self.client.get(reverse("home"))
        self.assertContains(page, "Уведомления в Telegram")
        self.assertContains(page, "Подключить")

    def test_page_shows_personal_link(self):
        with telegram_api():
            page = self.client.get(reverse("telegram"))
        self.assertContains(page, f"https://t.me/otdoner_alerts_bot?start={telegram.link_code(self.boss)}")
        self.assertContains(page, "Не подключено")

    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_page_explains_missing_token(self):
        page = self.client.get(reverse("telegram"))
        self.assertContains(page, "не задан TELEGRAM_BOT_TOKEN")

    def test_wrong_token_is_explained(self):
        with mock.patch("notifications.telegram.requests.post",
                        return_value=api_response(401, {"ok": False, "error_code": 401, "description": "Unauthorized"})):
            page = self.client.get(reverse("telegram"))
        self.assertContains(page, "Telegram не принял токен бота")

    def test_check_saves_chat_id_and_sends_test(self):
        code = telegram.link_code(self.boss)
        updates = [self.start_update("привет", chat_id=111), self.start_update(f"/start {code}", chat_id=777, update_id=2)]
        with telegram_api(updates=updates) as post:
            response = self.client.post(reverse("telegram_check"), follow=True)
        self.boss.refresh_from_db()
        self.assertEqual(self.boss.telegram_chat_id, "777")
        self.assertContains(response, "проверочное сообщение отправлено")
        sent = [c for c in post.call_args_list if c.args[0].endswith("/sendMessage")]
        self.assertEqual(sent[0].kwargs["json"]["chat_id"], "777")
        self.assertContains(response, "Подключено")

    def test_someone_elses_code_does_not_match(self):
        other = User.objects.create_user("87000000005", role=User.Role.MANAGER)
        with telegram_api(updates=[self.start_update(f"/start {telegram.link_code(other)}")]):
            response = self.client.post(reverse("telegram_check"), follow=True)
        self.boss.refresh_from_db()
        self.assertEqual(self.boss.telegram_chat_id, "")
        self.assertContains(response, "Бот пока не получил ваше «Старт»")

    def test_send_error_is_shown(self):
        self.boss.telegram_chat_id = "777"
        self.boss.save()
        with telegram_api(send_ok=False), self.assertLogs("otdoner"):
            response = self.client.post(reverse("telegram_test"), follow=True)
        self.assertContains(response, "сообщение не ушло")
        self.assertContains(response, "заблокировал бота")

    def test_shift_admin_is_sent_home(self):
        User.objects.create_user("87000000001", password="test12345")
        self.client.login(username="87000000001", password="test12345")
        self.assertRedirects(self.client.get(reverse("telegram")), reverse("home"), fetch_redirect_response=False)
