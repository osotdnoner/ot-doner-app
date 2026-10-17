"""
Тестовые данные для пробы на своём компьютере:

    python manage.py demo_setup

Создаёт тестовую точку (проверка территории выключена), управляющего, администратора
и трёх сотрудников. Чтобы попробовать и проверку территории, передайте координаты:

    python manage.py demo_setup --lat 43.238949 --lon 76.889709 --check-territory

Координаты своего места: Google Maps → правый клик по карте → нажать на числа (они скопируются).
"""

from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from points.models import Point
from staff.models import Employee

PASSWORD = "demo12345"


class Command(BaseCommand):
    help = "Создаёт тестовую точку, управляющего, администратора и сотрудников"

    def add_arguments(self, parser):
        parser.add_argument("--lat", type=float, help="Широта, например 43.238949")
        parser.add_argument("--lon", type=float, help="Долгота, например 76.889709")
        parser.add_argument("--radius", type=int, default=500, help="Радиус территории в метрах (для пробы — побольше)")
        parser.add_argument("--check-territory", action="store_true", help="Включить проверку территории")

    def handle(self, *args, **opts):
        if opts["check_territory"] and (opts["lat"] is None or opts["lon"] is None):
            raise CommandError("Для проверки территории укажите --lat и --lon")
        point, _ = Point.objects.update_or_create(
            name="Тестовая точка",
            defaults={
                "latitude": round(opts["lat"], 6) if opts["lat"] is not None else None,
                "longitude": round(opts["lon"], 6) if opts["lon"] is not None else None,
                "radius_m": opts["radius"], "require_location": opts["check_territory"], "address": "для пробы",
            },
        )

        manager, created = User.objects.get_or_create(
            username="87000000000",
            defaults={"first_name": "Управляющий", "role": User.Role.MANAGER, "is_staff": True, "is_superuser": True},
        )
        if created:
            manager.set_password(PASSWORD)
            manager.save()
        manager.points.add(point)

        admin, created = User.objects.get_or_create(
            username="87000000001", defaults={"first_name": "Админ", "role": User.Role.ADMIN},
        )
        if created:
            admin.set_password(PASSWORD)
            admin.save()
        admin.points.add(point)

        for name, position in [("Айдос Серикбаев", "cashier"), ("Гульнар Ахметова", "cleaner"), ("Ерлан Жумабаев", "doner")]:
            Employee.objects.get_or_create(point=point, full_name=name, defaults={"position": position})

        self.stdout.write(self.style.SUCCESS("Готово!"))
        territory = f"проверка территории включена, радиус {point.radius_m} м" if point.require_location else "проверка территории выключена"
        self.stdout.write(f"Точка: «{point}», {territory}")
        self.stdout.write(f"Администратор: логин 87000000001, пароль {PASSWORD} → http://127.0.0.1:8000/")
        self.stdout.write(f"Управляющий:   логин 87000000000, пароль {PASSWORD} → http://127.0.0.1:8000/panel/")
