"""
Выгрузка зарплатных данных в Google Таблицу.

Приложение отправляет строки на адрес Google Apps Script (веб-приложение внутри таблицы),
а скрипт записывает их на лист «Журнал». Строки двух видов:
  • «Смена» — один человек в одной смене: выход, опоздание, переработка и недоработка в часах,
    штрафы, бонусы, замена;
  • «Аванс» — деньги, выданные заранее.
Ещё приложение присылает список сотрудников: скрипт ведёт по нему лист «Ставки» — у каждого своя
ставка за дневную, ночную и полную смену (её вписывает шеф).
Если строка с таким ключом уже есть (админ поправил опоздание, шеф снял штраф), она обновляется.
Если смену отметили «Выходной», удалили замену или аванс — строка удаляется.
Ставки, цену часа, начисления, авансы и итоги за 1–15 и 16–конец месяца считают формулы самой таблицы.
Код скрипта — в файле payroll/google_apps_script.js, подключение — в README.

Пока в .env не задан GOOGLE_SHEET_URL, данные копятся в приложении и уйдут, когда адрес появится.
"""

import logging
import threading
import time
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

logger = logging.getLogger("otdoner.sheets")

BATCH = 200

# Колонки листа «Журнал» (A–Z). Порядок совпадает со скриптом в таблице.
HEADERS = [
    "Ключ", "Дата", "Филиал", "Что", "Смена", "Сотрудник", "Родной филиал", "Должность", "Выход", "Пришёл в",
    "Опоздание, мин", "Причина", "Переработка, ч", "Недоработка, ч", "Часы: почему", "Штраф, ₸", "За что штраф",
    "Бонус, ₸", "За что бонус", "Аванс, ₸", "Замена", "Комментарий", "Кто отметил", "Обновлено", "Часов в смене",
    "Код сотрудника",
]


def _local(dt, point):
    return timezone.localtime(dt, ZoneInfo(point.timezone))


def shift_key(attendance) -> str:
    # Ключ с буквами, чтобы Google не принял его за дату или время
    return f"S{attendance.shift_id}-E{attendance.employee_id}"


def advance_key(advance) -> str:
    return f"A{advance.id}"


def employee_key(employee) -> str:
    # По этому коду таблица находит личную ставку на листе «Ставки»
    return f"E{employee.id}"


def staff_row(e) -> list:
    """Строка листа «Ставки»: код, имя, родной филиал, должность, работает ли."""
    return [employee_key(e), e.full_name, e.home, e.get_position_display(), "работает" if e.is_active else "уволен"]


def shift_row(a) -> list:
    point = a.shift.point
    return [
        shift_key(a),
        # Дата в виде 2026-10-16: скрипт превращает её в настоящую дату для формул по периодам
        f"{_local(a.shift.planned_start, point):%Y-%m-%d}",
        point.name,
        "Смена",
        a.get_kind_display(),
        a.employee.full_name,
        a.employee.home,
        a.position_display,
        a.get_status_display(),
        f"{_local(a.arrived_at, point):%H:%M}" if a.arrived_at else "",
        a.late_minutes,
        a.reason,
        a.overtime_hours,
        a.undertime_hours,
        a.hours_reasons,
        a.fine_total,
        a.fine_reasons,
        a.bonus_total,
        "; ".join(b.reason for b in a.bonuses.all()),
        0,
        a.substitute_text,
        a.fine_note,
        str(a.shift.admin),
        f"{timezone.localtime(a.marked_at):%Y-%m-%d %H:%M}",
        a.shift_hours,
        employee_key(a.employee),
    ]


def advance_row(adv) -> list:
    e = adv.employee
    blank_shift = ["", "", 0, "", 0, 0, "", 0, "", 0, ""]  # выход … за что бонус (I–S)
    return [
        advance_key(adv),
        f"{adv.given_on:%Y-%m-%d}",
        adv.point.name if adv.point_id else e.home,
        "Аванс",
        "",
        e.full_name,
        e.home,
        e.get_position_display(),
        *blank_shift,
        adv.amount,
        "",
        adv.comment,
        str(adv.given_by or ""),
        f"{timezone.localtime(adv.created_at):%Y-%m-%d %H:%M}",
        0,
        employee_key(e),
    ]


def _send(rows: list, deletes: list, staff: list | None = None) -> str:
    """Один запрос к таблице. Возвращает текст ошибки или пустую строку."""
    payload = {"secret": settings.GOOGLE_SHEET_SECRET, "headers": HEADERS, "rows": rows, "delete": deletes}
    if staff is not None:
        payload["staff"] = staff
    try:
        response = requests.post(settings.GOOGLE_SHEET_URL, json=payload, timeout=30)
        data = response.json() if response.ok else {}
        if not data.get("ok"):
            return data.get("error") or f"Таблица ответила кодом {response.status_code}."
    except requests.RequestException as exc:
        return f"Нет связи с Google: {exc.__class__.__name__}."
    except ValueError:
        return "Таблица ответила не тем: проверьте, что адрес GOOGLE_SHEET_URL заканчивается на /exec."
    return ""


def push(ids=(), advance_ids=(), delete_keys=()) -> tuple[int, str]:
    """Отправляет смены, авансы и удаления. Возвращает (сколько строк отправлено, ошибка)."""
    from .models import Advance, Attendance, SheetDeletion

    if not settings.GOOGLE_SHEET_URL:
        return 0, "Не задан GOOGLE_SHEET_URL — данные ждут в приложении."
    shifts = list(
        Attendance.objects.filter(id__in=list(ids))
        .select_related("shift__point", "shift__admin", "employee__point", "substitute_for")
        .prefetch_related("bonuses", "penalties", "extra_hours")
    )
    advances = list(Advance.objects.filter(id__in=list(advance_ids)).select_related("employee__point", "point", "given_by"))
    deletes = list(delete_keys) + [shift_key(a) for a in shifts if a.status == a.Status.OFF]
    rows = [shift_row(a) for a in shifts if a.status != a.Status.OFF] + [advance_row(x) for x in advances]
    if not rows and not deletes:
        return 0, ""

    started = timezone.now()
    error = _send(rows, deletes)
    shift_ids = [a.id for a in shifts]
    adv_ids = [x.id for x in advances]
    if error:
        logger.warning("Не удалось выгрузить в таблицу: %s", error)
        Attendance.objects.filter(id__in=shift_ids).update(sync_error=error[:500])
        Advance.objects.filter(id__in=adv_ids).update(sync_error=error[:500])
        return 0, error
    now = timezone.now()
    # Если строку успели изменить во время отправки, оставляем её в очереди
    Attendance.objects.filter(id__in=shift_ids, marked_at__lte=started).update(needs_sync=False, synced_at=now, sync_error="")
    Attendance.objects.filter(id__in=shift_ids, marked_at__gt=started).update(synced_at=now, sync_error="")
    Advance.objects.filter(id__in=adv_ids, created_at__lte=started).update(needs_sync=False, synced_at=now, sync_error="")
    SheetDeletion.objects.filter(key__in=list(delete_keys)).delete()
    return len(rows) + len(deletes), ""


def _push_in_background(ids, advance_ids, delete_keys):
    try:
        push(ids, advance_ids, delete_keys)
    except Exception:
        logger.exception("Ошибка выгрузки в таблицу")
    finally:
        connection.close()


def queue(ids=(), advances=(), deletes=()):
    """Отправить после сохранения. Без адреса таблицы — тихо ждём (удаления запоминаются)."""
    from .models import SheetDeletion

    for k in deletes:
        SheetDeletion.objects.get_or_create(key=k)
    if not settings.GOOGLE_SHEET_URL:
        return
    ids, advances, deletes = list(ids), list(advances), list(deletes)
    if not settings.BACKGROUND_ASYNC:
        push(ids, advances, deletes)
        return
    transaction.on_commit(
        lambda: threading.Thread(target=_push_in_background, args=(ids, advances, deletes), daemon=True).start()
    )


def push_staff() -> str:
    """Список сотрудников для листа «Ставки». Ставки там не трогаются — их вписывает шеф."""
    from staff.models import Employee

    if not settings.GOOGLE_SHEET_URL:
        return "Не задан GOOGLE_SHEET_URL."
    staff = [staff_row(e) for e in Employee.objects.select_related("point").order_by("point__name", "full_name")]
    error = _send([], [], staff)
    if error:
        logger.warning("Не удалось отправить список сотрудников: %s", error)
    return error


def _push_staff_in_background():
    try:
        push_staff()
    except Exception:
        logger.exception("Ошибка выгрузки списка сотрудников")
    finally:
        connection.close()


_staff_lock = threading.Lock()
_staff_scheduled = False


def _push_staff_soon():
    global _staff_scheduled
    time.sleep(3)  # несколько изменений подряд (например, загрузка списка) — одной отправкой
    with _staff_lock:
        _staff_scheduled = False
    _push_staff_in_background()


def queue_staff():
    """Сотрудника добавили или поменяли — обновить лист «Ставки» после сохранения."""
    global _staff_scheduled
    if not settings.GOOGLE_SHEET_URL:
        return
    if not settings.BACKGROUND_ASYNC:
        push_staff()
        return
    with _staff_lock:
        if _staff_scheduled:
            return
        _staff_scheduled = True
    transaction.on_commit(lambda: threading.Thread(target=_push_staff_soon, daemon=True).start())


def push_pending(limit: int = 1000) -> tuple[int, str]:
    """Всё, что ещё не дошло до таблицы (сбой связи, адрес задали позже, таблицу перестроили)."""
    from .models import Advance, Attendance, SheetDeletion

    deletes = list(SheetDeletion.objects.values_list("key", flat=True)[:limit])
    advances = list(Advance.objects.filter(needs_sync=True).values_list("id", flat=True)[:limit])
    ids = list(Attendance.objects.filter(needs_sync=True).order_by("id").values_list("id", flat=True)[:limit])
    sent, error = 0, ""
    first = True
    for start in range(0, max(len(ids), 1), BATCH):
        chunk = ids[start:start + BATCH]
        count, error = push(chunk, advances if first else (), deletes if first else ())
        first = False
        sent += count
        if error:
            break
    return sent, error


def check_connection() -> tuple[bool, str]:
    """Проверка связи с таблицей: пустой запрос с секретным словом. Таблица его принимает и ничего не меняет."""
    if not settings.GOOGLE_SHEET_URL:
        return False, "Не задан GOOGLE_SHEET_URL."
    error = _send([], [])
    return (not error), error
