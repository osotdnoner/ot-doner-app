"""
Выгрузка зарплатных данных в Google Таблицу.

Приложение отправляет строки на адрес Google Apps Script (веб-приложение внутри таблицы),
а скрипт записывает их на лист «Журнал». Строки двух видов:
  • «Смена» — один человек в одной смене: выход, опоздание, штрафы, бонусы, замена;
  • «Аванс» — деньги, выданные заранее.
Если строка с таким ключом уже есть (админ поправил опоздание, шеф снял штраф), она обновляется.
Если смену отметили «Выходной», удалили замену или аванс — строка удаляется.
Ставки, начисления, авансы и итоги за 1–15 и 16–конец месяца считают формулы самой таблицы.
Код скрипта — в файле payroll/google_apps_script.js, подключение — в README.

Пока в .env не задан GOOGLE_SHEET_URL, данные копятся в приложении и уйдут, когда адрес появится.
"""

import logging
import threading
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

logger = logging.getLogger("otdoner.sheets")

BATCH = 200

# Колонки листа «Журнал» (A–U). Порядок совпадает со скриптом в таблице.
HEADERS = [
    "Ключ", "Дата", "Филиал", "Что", "Смена", "Сотрудник", "Родной филиал", "Должность", "Выход", "Пришёл в",
    "Опоздание, мин", "Причина", "Штраф, ₸", "За что штраф", "Бонус, ₸", "За что бонус", "Аванс, ₸", "Замена",
    "Комментарий", "Кто отметил", "Обновлено",
]


def _local(dt, point):
    return timezone.localtime(dt, ZoneInfo(point.timezone))


def shift_key(attendance) -> str:
    # Ключ с буквами, чтобы Google не принял его за дату или время
    return f"S{attendance.shift_id}-E{attendance.employee_id}"


def advance_key(advance) -> str:
    return f"A{advance.id}"


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
        a.fine_total,
        a.fine_reasons,
        a.bonus_total,
        "; ".join(b.reason for b in a.bonuses.all()),
        0,
        a.substitute_text,
        a.fine_note,
        str(a.shift.admin),
        f"{timezone.localtime(a.marked_at):%Y-%m-%d %H:%M}",
    ]


def advance_row(adv) -> list:
    return [
        advance_key(adv),
        f"{adv.given_on:%Y-%m-%d}",
        adv.point.name if adv.point_id else adv.employee.home,
        "Аванс",
        "",
        adv.employee.full_name,
        adv.employee.home,
        adv.employee.get_position_display(),
        "",
        "",
        0,
        "",
        0,
        "",
        0,
        "",
        adv.amount,
        "",
        adv.comment,
        str(adv.given_by or ""),
        f"{timezone.localtime(adv.created_at):%Y-%m-%d %H:%M}",
    ]


def _send(rows: list, deletes: list) -> str:
    """Один запрос к таблице. Возвращает текст ошибки или пустую строку."""
    payload = {"secret": settings.GOOGLE_SHEET_SECRET, "headers": HEADERS, "rows": rows, "delete": deletes}
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
        .prefetch_related("bonuses", "penalties")
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
