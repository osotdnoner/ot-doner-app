"""
Обновляет список сотрудников на листе «Ставки» и досылает в Google Таблицу всё, что туда ещё
не попало: после сбоя связи, после того как задали адрес таблицы или перестроили её. На Render запускается при каждом старте сайта.

    python manage.py sync_sheet
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from payroll.sheets import push_pending, push_staff


class Command(BaseCommand):
    help = "Отправляет в Google Таблицу все строки, которые ещё не дошли"

    def handle(self, *args, **options):
        if not settings.GOOGLE_SHEET_URL:
            self.stdout.write("GOOGLE_SHEET_URL не задан — пропускаю.")
            return
        error = push_staff()
        self.stdout.write(f"Список сотрудников: {'ошибка — ' + error if error else 'отправлен'}.")
        total = 0
        for _ in range(20):  # за раз уходит до 1000 строк
            sent, error = push_pending()
            total += sent
            if error:
                self.stdout.write(f"Отправлено строк: {total}. Ошибка: {error}")
                return
            if not sent:
                break
        self.stdout.write(f"Отправлено строк: {total}.")
