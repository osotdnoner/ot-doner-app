from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from shifts import views as shift_views

admin.site.site_header = "ОТ Донер — управление"
admin.site.site_title = "ОТ Донер"
admin.site.index_title = "Настройки и данные"

urlpatterns = [
    path("", shift_views.home, name="home"),
    path("checkin/", shift_views.checkin, name="checkin"),
    path("shift/<int:shift_id>/late-reason/", shift_views.late_reason, name="late_reason"),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("panel/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
