"""Формы входа и добавления пользователя: логин — номер телефона в любом виде."""

import re

from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from points.models import Point

from .models import User


def normalize_login(value: str) -> str:
    """'+7 701 123 45 67', '8 (701) 123-45-67', '7011234567' → '87011234567'. Не телефон — как есть."""
    value = (value or "").strip()
    digits = re.sub(r"\D", "", value)
    if re.fullmatch(r"[\d\s()+\-]+", value or "x"):
        if len(digits) == 10:
            return "8" + digits
        if len(digits) == 11 and digits[0] in "78":
            return "8" + digits[1:]
    return value


class PhoneLoginForm(AuthenticationForm):
    """Вход: номер можно ввести с +7, пробелами и скобками."""

    def clean_username(self):
        return normalize_login(self.cleaned_data.get("username", ""))


class UserAddForm(UserCreationForm):
    """Добавление пользователя на одной странице: номер, имя, роль, точки, пароль."""

    points = forms.ModelMultipleChoiceField(
        label="Точки", queryset=Point.objects.filter(is_active=True), required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text="Где этот человек работает. Администратор отмечается и отмечает сотрудников только на этих точках. "
                  "Если у двух точек один администратор (например, 27 и 16 мкрн), отметьте обе — "
                  "при «Я пришёл» он выберет, где сегодня.",
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "first_name", "last_name", "role", "points")

    def clean_username(self):
        username = normalize_login(self.cleaned_data.get("username", ""))
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError("Пользователь с таким номером уже есть.")
        return username

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("role") == User.Role.ADMIN and not cleaned.get("points"):
            self.add_error("points", "Выберите точки, на которых работает этот администратор.")
        return cleaned
