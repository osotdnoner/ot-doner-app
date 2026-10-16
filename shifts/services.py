"""
Логика отметки «Я пришёл на смену».

Здесь нет ничего про веб-страницы — только правила. Поэтому их легко проверять
автотестами (см. shifts/tests.py). Главное правило: место и время проверяет сервер,
телефон только присылает координаты и селфи.
"""

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from points.models import Point

from .models import CheckinAttempt, Shift

EARTH_RADIUS_M = 6_371_000


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Расстояние между двумя точками на карте в метрах (формула гаверсинусов)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def format_distance(meters: float) -> str:
    if meters < 1000:
        return f"{round(meters / 10) * 10:.0f} м"
    return f"{meters / 1000:.1f} км".replace(".", ",")


@dataclass(frozen=True)
class ShiftSlot:
    """Смена по графику точки: дневная или ночная, с началом и концом."""

    kind: str
    start: datetime
    end: datetime


def shift_slots_around(point: Point, now: datetime) -> list[ShiftSlot]:
    """Смены точки со вчера по послезавтра, по порядку. Конец смены — начало следующей."""
    tz = ZoneInfo(point.timezone)
    today = now.astimezone(tz).date()
    starts = []
    for offset in (-1, 0, 1, 2):
        day = today + timedelta(days=offset)
        starts.append((Shift.Kind.DAY, datetime.combine(day, point.day_shift_start, tzinfo=tz)))
        starts.append((Shift.Kind.NIGHT, datetime.combine(day, point.night_shift_start, tzinfo=tz)))
    starts.sort(key=lambda item: item[1])
    return [
        ShiftSlot(kind=kind, start=start, end=next_start)
        for (kind, start), (_, next_start) in zip(starts, starts[1:])
    ]


def current_slot(point: Point, now: datetime, early_minutes: int | None = None) -> ShiftSlot | None:
    """
    Смена, на которую человек отмечается сейчас.

    Отметиться можно за early_minutes до начала. Если окна двух смен пересекаются
    (например, в 9:30 ночная ещё идёт, а на дневную уже можно отмечаться),
    выбираем более позднюю — это та смена, на которую человек пришёл.
    """
    if early_minutes is None:
        early_minutes = settings.CHECKIN_EARLY_MINUTES
    early = timedelta(minutes=early_minutes)
    candidates = [s for s in shift_slots_around(point, now) if s.start - early <= now < s.end]
    return max(candidates, key=lambda s: s.start) if candidates else None


def late_minutes(slot_start: datetime, now: datetime, grace_minutes: int = 0) -> int:
    """Опоздание в целых минутах. В пределах допуска опоздания нет."""
    minutes = int((now - slot_start).total_seconds() // 60)
    return minutes if minutes > grace_minutes else 0


@dataclass
class CheckinResult:
    status: str  # ok | late | outside | low_accuracy | no_slot | already | no_point
    message: str
    shift: Shift | None = None
    point: Point | None = None
    distance_m: float | None = None


def perform_checkin(user, lat: float, lon: float, accuracy: float, selfie, *,
                    now: datetime | None = None, ip: str | None = None) -> CheckinResult:
    """Принимает или отклоняет отметку. Каждая попытка записывается в журнал."""
    now = now or timezone.now()
    points = list(user.points.filter(is_active=True))

    def log(result: str, point=None, dist=None):
        CheckinAttempt.objects.create(
            admin=user, point=point, latitude=round(lat, 6), longitude=round(lon, 6),
            accuracy_m=accuracy, distance_m=dist, result=result, ip_address=ip,
        )

    if not points:
        log(CheckinAttempt.Result.NO_POINT)
        return CheckinResult("no_point", "Вы не закреплены ни за одной точкой. Обратитесь к управляющему.")

    # Ближайшая из точек, где работает этот администратор
    point, dist = min(
        ((p, distance_m(lat, lon, float(p.latitude), float(p.longitude))) for p in points),
        key=lambda pair: pair[1],
    )

    if accuracy > settings.CHECKIN_MAX_ACCURACY_M:
        log(CheckinAttempt.Result.LOW_ACCURACY, point, dist)
        return CheckinResult(
            "low_accuracy",
            f"Телефон определяет место неточно (±{accuracy:.0f} м). "
            "Подойдите ближе к окну или выходу и попробуйте ещё раз.",
            point=point, distance_m=dist,
        )

    if dist > point.radius_m:
        log(CheckinAttempt.Result.OUTSIDE, point, dist)
        return CheckinResult(
            "outside",
            f"Вы не на территории ресторана. До точки «{point.name}» — {format_distance(dist)}.",
            point=point, distance_m=dist,
        )

    slot = current_slot(point, now)
    if slot is None:
        log(CheckinAttempt.Result.NO_SLOT, point, dist)
        return CheckinResult("no_slot", "Сейчас нет смены, на которую можно отметиться.", point=point, distance_m=dist)

    existing = Shift.objects.filter(admin=user, planned_start=slot.start).first()
    if existing:
        log(CheckinAttempt.Result.ALREADY, point, dist)
        return CheckinResult("already", "Вы уже отметились на эту смену.", shift=existing, point=point, distance_m=dist)

    late = late_minutes(slot.start, now, point.late_grace_minutes)
    try:
        with transaction.atomic():
            shift = Shift.objects.create(
                point=point, admin=user, kind=slot.kind,
                planned_start=slot.start, planned_end=slot.end,
                checked_in_at=now, latitude=round(lat, 6), longitude=round(lon, 6),
                accuracy_m=accuracy, distance_m=dist, selfie=selfie, late_minutes=late,
            )
    except IntegrityError:  # двойное нажатие кнопки
        existing = Shift.objects.get(admin=user, planned_start=slot.start)
        return CheckinResult("already", "Вы уже отметились на эту смену.", shift=existing, point=point, distance_m=dist)

    log(CheckinAttempt.Result.OK, point, dist)
    if late:
        return CheckinResult("late", f"Вы опоздали на {shift.late_human}.", shift=shift, point=point, distance_m=dist)
    return CheckinResult("ok", "Вы на смене. Отметка принята вовремя.", shift=shift, point=point, distance_m=dist)


def current_shift_for(user, now: datetime | None = None) -> Shift | None:
    """Открытая смена администратора, которая идёт сейчас."""
    now = now or timezone.now()
    return (
        Shift.objects.filter(admin=user, status=Shift.Status.OPEN, planned_end__gt=now)
        .select_related("point")
        .order_by("-planned_start")
        .first()
    )
