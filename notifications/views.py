from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from . import telegram
from .models import Notification
from .service import send_test


def _report_test(request, user):
    for channel, outcome in send_test(user, channels=[Notification.Channel.TELEGRAM]):
        if outcome.ok:
            messages.success(request, f"{channel}: проверочное сообщение отправлено. Посмотрите в чате с ботом.")
        else:
            messages.error(request, f"{channel}: сообщение не ушло. {outcome.error}")


@login_required
def telegram_page(request):
    """Экран «Уведомления в Telegram» для управляющего и собственника."""
    user = request.user
    if user.is_shift_admin:
        return redirect("home")
    link, problem = None, None
    try:
        link = telegram.connect_link(user)
    except telegram.TelegramError as exc:
        problem = str(exc)
    return render(request, "notifications/telegram.html", {
        "link": link,
        "problem": problem,
        "recent": user.notifications.filter(channel=Notification.Channel.TELEGRAM).order_by("-created_at")[:5],
    })


@login_required
@require_POST
def telegram_check(request):
    """«Я нажал Старт»: находим сообщение боту, запоминаем chat ID и сразу шлём проверку."""
    user = request.user
    if user.is_shift_admin:
        return redirect("home")
    try:
        chat_id = telegram.find_chat_id(user)
    except telegram.TelegramError as exc:
        messages.error(request, str(exc))
        return redirect("telegram")
    if not chat_id:
        messages.error(
            request,
            "Бот пока не получил ваше «Старт». Нажмите «Открыть бота», в Telegram нажмите «Старт» "
            "(или «Перезапустить»), вернитесь сюда и нажмите «Проверить» ещё раз.",
        )
        return redirect("telegram")
    user.telegram_chat_id = chat_id
    user.save(update_fields=["telegram_chat_id"])
    _report_test(request, user)
    return redirect("telegram")


@login_required
@require_POST
def telegram_test(request):
    if not request.user.is_shift_admin:
        if request.user.telegram_chat_id:
            _report_test(request, request.user)
        else:
            messages.error(request, "Сначала подключите Telegram.")
    return redirect("telegram")
