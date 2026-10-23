"""
Филиалы «ОТ Донер» с их графиками смен. Команду можно запускать сколько угодно раз:
она создаёт недостающие филиалы и обновляет время смен у существующих.

    python manage.py setup_branches
    python manage.py setup_branches --with-demo-users   # для тестового сайта: тестовые админ и управляющий на всех филиалах

Полная смена считается сама: с начала дневной до конца ночной.
"""

from datetime import time

from django.core.management.base import BaseCommand

from accounts.models import User
from points.models import Point

# название, начало и конец дневной смены, начало и конец ночной (None — ночной нет)
BRANCHES = [
    ("27 мкрн", time(10), time(22), time(22), time(10)),
    ("16 мкрн", time(10), time(23), None, None),
    ("17 мкрн", time(10), time(18), time(18), time(2)),
    ("14 мкрн", time(12), time(18), time(18), time(2)),
]

# Должности на каждом филиале — для листа «Ставки» в зарплатной таблице
POSITIONS = {
    "27 мкрн": ["Донерщик", "Пиццерист", "Кассир", "Раннер доставки", "Раннер кассы", "Техперсонал"],
    "16 мкрн": ["Донерщик", "Кассир"],
    "17 мкрн": ["Донерщик", "Пиццерист", "Кассир", "Раннер кассы", "Техперсонал кухни", "Техперсонал зала"],
    "14 мкрн": ["Донерщик", "Пиццерист", "Кассир", "Раннер кассы", "Техперсонал"],
}


class Command(BaseCommand):
    help = "Создаёт или обновляет 4 филиала с графиками смен"

    def add_arguments(self, parser):
        parser.add_argument("--with-demo-users", action="store_true",
                            help="Закрепить тестовых администратора и управляющего за всеми филиалами")

    def handle(self, *args, **opts):
        points = []
        for name, day_start, day_end, night_start, night_end in BRANCHES:
            defaults = {"day_shift_start": day_start, "day_shift_end": day_end, "has_night_shift": night_start is not None}
            if night_start is not None:
                defaults.update(night_shift_start=night_start, night_shift_end=night_end)
            point, created = Point.objects.update_or_create(name=name, defaults=defaults)
            points.append(point)
            self.stdout.write(f"{'Создан' if created else 'Обновлён'} филиал «{point}»: {point.schedule_text()}")

        if opts["with_demo_users"]:
            for username in ("87000000000", "87000000001"):
                user = User.objects.filter(username=username).first()
                if user:
                    user.points.add(*points)
            self.stdout.write("Тестовые администратор и управляющий закреплены за всеми филиалами.")
