"""
Выгрузка зарплатных данных в Google Таблицу.

Приложение отправляет строки на адрес Google Apps Script (веб-приложение внутри таблицы),
а скрипт записывает их на лист «Смены»: одна строка = один человек в одной смене.
Если строка с таким ключом уже есть (админ поправил опоздание, шеф снял штраф), она обновляется.
Если сотрудника переотметили как «Выходной», его строка удаляется.
Ставки, суммы к выплате и итоги за 1–15 и 16–конец месяца считают формулы самой таблицы.
Код скрипта — в файле payroll/google_apps_script.js, подключение — в README.

Пока в .env не задан GOOGLE_SHEET_URL, данные копятся в приложении и уйдут, когда адрес появится.
"""

import logging
import threading

import requests
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

logger = logging.getLogger("otdoner.sheets")

BATCH = 200


def _local(dt, point):
    from zoneinfo import ZoneInfo
    return timezone.localtime(dt, ZoneInfo(point.timezone))


# Колонки листа: заголовок и как получить значение из «выхода на смену»
COLUMNS = [
    # Ключ с буквами, чтобы Google не принял его за дату или время
    ("Ключ", lambda a: f"S{a.shift_id}-E{a.employee_id}"),
    # Дата в виде 2026-10-16: Google в любой стране понимает её как дату (для формул по месяцам)
    ("Дата", lambda a: f"{_local(a.shift.planned_start, a.shift.point):%Y-%m-%d}"),
    ("Филиал", lambda a: a.shift.point.name),
    ("Смена", lambda a: a.get_kind_display()),
    ("Сотрудник", lambda a: a.employee.full_name),
    ("Должность", lambda a: a.employee.get_position_display()),
    ("Выход", lambda a: a.get_status_display()),
    ("Пришёл в", lambda a: f"{_local(a.arrived_at, a.shift.point):%H:%M}" if a.arrived_at else ""),
    ("Опоздание, мин", lambda a: a.late_minutes),
    ("Причина", lambda a: a.reason),
    ("Штраф, ₸", lambda a: a.fine),
    ("Бонус, ₸", lambda a: a.bonus_total),
    ("За что бонус", lambda a: "; ".join(b.reason for b in a.bonuses.all())),
    ("Комментарий к штрафу", lambda a: a.fine_note),
    ("Администратор смены", lambda a: str(a.shift.admin)),
    ("Обновлено", lambda a: f"{timezone.localtime(a.marked_at):%Y-%m-%d %H:%M}"),
]
HEADERS = [title for title, _ in COLUMNS]


def row(attendance) -> list:
    return [get(attendance) for _, get in COLUMNS]


def key(attendance) -> str:
    return COLUMNS[0][1](attendance)


def push(ids: list[int]) -> tuple[int, str]:
    """Отправляет строки в таблицу. Возвращает (сколько отправлено, ошибка)."""
    from .models import Attendance

    url = settings.GOOGLE_SHEET_URL
    if not url:
        return 0, "Не задан GOOGLE_SHEET_URL — данные ждут в приложении."
    items = list(
        Attendance.objects.filter(id__in=ids)
        .select_related("shift__point", "shift__admin", "employee")
        .prefetch_related("bonuses")
    )
    if not items:
        return 0, ""
    started = timezone.now()
    payload = {
        "secret": settings.GOOGLE_SHEET_SECRET,
        "headers": HEADERS,
        "rows": [row(a) for a in items if a.status != a.Status.OFF],
        "delete": [key(a) for a in items if a.status == a.Status.OFF],
    }
    error = ""
    try:
        response = requests.post(url, json=payload, timeout=30)
        data = response.json() if response.ok else {}
        if not data.get("ok"):
            error = data.get("error") or f"Таблица ответила кодом {response.status_code}."
    except requests.RequestException as exc:
        error = f"Нет связи с Google: {exc.__class__.__name__}."
    except ValueError:
        error = "Таблица ответила не тем: проверьте, что адрес GOOGLE_SHEET_URL заканчивается на /exec."

    done = [a.id for a in items]
    if error:
        logger.warning("Не удалось выгрузить в таблицу: %s", error)
        Attendance.objects.filter(id__in=done).update(sync_error=error[:500])
        return 0, error
    # Если строку успели изменить во время отправки, оставляем её в очереди
    Attendance.objects.filter(id__in=done, marked_at__lte=started).update(
        needs_sync=False, synced_at=timezone.now(), sync_error="",
    )
    Attendance.objects.filter(id__in=done, marked_at__gt=started).update(synced_at=timezone.now(), sync_error="")
    return len(done), ""


def _push_in_background(ids):
    try:
        push(ids)
    except Exception:
        logger.exception("Ошибка выгрузки в таблицу")
    finally:
        connection.close()


def queue(ids: list[int]):
    """Отправить строки после сохранения. Без адреса таблицы — тихо ждём."""
    if not settings.GOOGLE_SHEET_URL:
        return
    if not settings.BACKGROUND_ASYNC:
        push(ids)
        return
    transaction.on_commit(lambda: threading.Thread(target=_push_in_background, args=(ids,), daemon=True).start())


def push_pending(limit: int = 1000) -> tuple[int, str]:
    """Всё, что ещё не дошло до таблицы (сбой связи, адрес задали позже). Запускает send_reminders."""
    from .models import Attendance

    ids = list(Attendance.objects.filter(needs_sync=True).order_by("id").values_list("id", flat=True)[:limit])
    sent, error = 0, ""
    for start in range(0, len(ids), BATCH):
        count, error = push(ids[start:start + BATCH])
        sent += count
        if error:
            break
    return sent, error


def check_connection() -> tuple[bool, str]:
    """Проверка связи с таблицей: пустой запрос с секретным словом. Таблица его принимает и ничего не меняет."""
    url = settings.GOOGLE_SHEET_URL
    if not url:
        return False, "Не задан GOOGLE_SHEET_URL."
    payload = {"secret": settings.GOOGLE_SHEET_SECRET, "headers": HEADERS, "rows": [], "delete": []}
    try:
        response = requests.post(url, json=payload, timeout=30)
        data = response.json()
    except requests.RequestException as exc:
        return False, f"Нет связи с Google: {exc.__class__.__name__}."
    except ValueError:
        return False, "Таблица ответила не тем: проверьте, что адрес GOOGLE_SHEET_URL заканчивается на /exec."
    if not data.get("ok"):
        return False, data.get("error") or f"Таблица ответила кодом {response.status_code}."
    return True, ""
