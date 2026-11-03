"""Аккаунты администраторов: добавление в панели и вход по номеру телефона в любом виде."""

from django.test import TestCase, override_settings
from django.urls import reverse

from points.models import Point

from .forms import normalize_login
from .models import User


class LoginNumberTests(TestCase):
    def test_normalize(self):
        for raw in ("87011234567", "+7 701 123 45 67", "8 (701) 123-45-67", "7011234567", "77011234567"):
            self.assertEqual(normalize_login(raw), "87011234567")
        self.assertEqual(normalize_login(" boss "), "boss")

    def test_login_with_plus_seven(self):
        User.objects.create_user("87011234567", password="test12345")
        response = self.client.post(reverse("login"), {"username": "+7 701 123 45 67", "password": "test12345"})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.client.session["_auth_user_id"]), User.objects.get().id)


@override_settings(GOOGLE_SHEET_URL="")
class AddAdminTests(TestCase):
    def setUp(self):
        self.p27 = Point.objects.create(name="27 мкрн")
        self.p16 = Point.objects.create(name="16 мкрн")
        self.p14 = Point.objects.create(name="14 мкрн")
        User.objects.create_superuser("87000000000", password="test12345", role=User.Role.MANAGER)
        self.client.login(username="87000000000", password="test12345")
        self.url = reverse("admin:accounts_user_add")

    def form(self, **extra):
        data = {"username": "+7 701 555 22 11", "first_name": "Айдос", "last_name": "", "role": "admin",
                "points": [self.p27.id, self.p16.id], "password1": "Kaspi-2026-ot", "password2": "Kaspi-2026-ot",
                "usable_password": "true"}
        data.update(extra)
        return data

    def test_admin_for_two_points_in_one_step(self):
        page = self.client.get(self.url)
        self.assertContains(page, "27 мкрн")
        self.assertContains(page, "один администратор")
        response = self.client.post(self.url, self.form())
        self.assertEqual(response.status_code, 302, response.content[:3000])
        user = User.objects.get(username="87015552211")
        self.assertEqual(set(user.points.all()), {self.p27, self.p16})
        self.assertEqual((user.role, user.is_staff, user.first_name), ("admin", False, "Айдос"))
        # и может войти в приложение
        self.client.logout()
        self.assertTrue(self.client.login(username="87015552211", password="Kaspi-2026-ot"))

    def test_admin_needs_points_and_unique_number(self):
        response = self.client.post(self.url, self.form(points=[]))
        self.assertContains(response, "Выберите точки")
        self.client.post(self.url, self.form())
        response = self.client.post(self.url, self.form(username="87015552211"))
        self.assertContains(response, "уже есть")

    def test_manager_gets_panel(self):
        response = self.client.post(self.url, self.form(username="87010000009", role="manager", points=[]))
        self.assertEqual(response.status_code, 302, response.content[:3000])
        self.assertTrue(User.objects.get(username="87010000009").is_staff)
