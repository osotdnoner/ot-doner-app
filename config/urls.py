from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, re_path
from django.views.static import serve as serve_file

from checklists import views as checklist_views
from notifications import views as notification_views
from payroll import views as payroll_views
from shifts import views as shift_views

admin.site.site_header = "ОТ Донер — управление"
admin.site.site_title = "ОТ Донер"
admin.site.index_title = "Настройки и данные"

urlpatterns = [
    path("", shift_views.home, name="home"),
    path("checkin/", shift_views.checkin, name="checkin"),
    path("shift/<int:shift_id>/late-reason/", shift_views.late_reason, name="late_reason"),
    path("shift/<int:shift_id>/staff/", payroll_views.attendance_page, name="attendance"),
    path("shift/<int:shift_id>/staff/<int:employee_id>/mark/", payroll_views.mark_view, name="attendance_mark"),
    path("shift/<int:shift_id>/staff/<int:employee_id>/bonus/", payroll_views.bonus_view, name="attendance_bonus"),
    path("shift/<int:shift_id>/bonus/<int:bonus_id>/delete/", payroll_views.bonus_delete_view, name="bonus_delete"),
    path("shift/<int:shift_id>/check/<slug:code>/", checklist_views.run_page, name="checklist"),
    path("run/<int:run_id>/item/<int:item_id>/<int:toilet_no>/", checklist_views.answer_item_view, name="answer_item"),
    path("run/<int:run_id>/staff/<int:employee_id>/", checklist_views.answer_staff_view, name="answer_staff"),
    path("run/<int:run_id>/finish/", checklist_views.finish_view, name="finish_run"),
    path("task/<int:task_id>/close/", checklist_views.close_task_view, name="close_task"),
    path("telegram/", notification_views.telegram_page, name="telegram"),
    path("telegram/check/", notification_views.telegram_check, name="telegram_check"),
    path("telegram/test/", notification_views.telegram_test, name="telegram_test"),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("panel/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
elif settings.SERVE_MEDIA:
    urlpatterns += [re_path(r"^media/(?P<path>.*)$", serve_file, {"document_root": settings.MEDIA_ROOT})]
