"""
Настройки проекта «ОТ Донер — приложение администратора».

Всё, что отличается между вашим компьютером и сервером (пароли, база, ключи),
берётся из файла .env (пример — в .env.example). Сам файл .env в GitHub не попадает.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


# --- Безопасность ----------------------------------------------------------
DEBUG = env_bool("DEBUG", True)
SECRET_KEY = os.getenv("SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("Задайте SECRET_KEY в файле .env на сервере")
    SECRET_KEY = "dev-only-key-not-for-production"

ALLOWED_HOSTS = [h.strip() for h in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.getenv("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

if not DEBUG:
    # Камера и геолокация в браузере работают только по HTTPS
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True


# --- Приложения ------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Наши модули (см. «Архитектура системы» в ТЗ)
    "accounts",  # пользователи и роли
    "points",    # точки (рестораны)
    "shifts",    # смены и отметка «Я пришёл»
    "staff",     # сотрудники точек
    "checklists",  # чек-листы приёма смены, ответы, задачи
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"


# --- База данных -----------------------------------------------------------
# На компьютере по умолчанию используется простой файл SQLite.
# На сервере в .env пишется DATABASE_URL=postgres://user:password@host:5432/name
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}


# --- Пользователи ----------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"
LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "home"
LOGOUT_REDIRECT_URL = "login"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator", "OPTIONS": {"min_length": 6}},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
]


# --- Язык и время ----------------------------------------------------------
LANGUAGE_CODE = "ru"
# Казахстан с 1 марта 2024 года живёт в одном поясе UTC+5.
# Время смен считается в часовом поясе каждой точки (поле «Часовой пояс»).
TIME_ZONE = "Asia/Almaty"
USE_I18N = True
USE_TZ = True


# --- Файлы -----------------------------------------------------------------
STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

# Фото (селфи, санузлы, униформа). Позже переедут в отдельное хранилище S3.
MEDIA_URL = "media/"
MEDIA_ROOT = Path(os.getenv("MEDIA_ROOT", BASE_DIR / "media"))

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# --- Правила отметки «Я пришёл» --------------------------------------------
# За сколько минут до начала смены можно отметиться
CHECKIN_EARLY_MINUTES = int(os.getenv("CHECKIN_EARLY_MINUTES", 60))
# Если GPS определяет место хуже, чем на столько метров, просим подождать
CHECKIN_MAX_ACCURACY_M = int(os.getenv("CHECKIN_MAX_ACCURACY_M", 150))
# Через сколько минут после начала смены сообщать управляющему, что никто не отметился
MISSED_CHECKIN_ALERT_MINUTES = int(os.getenv("MISSED_CHECKIN_ALERT_MINUTES", 10))


# --- Правила чек-листов ---------------------------------------------------
# Сколько пунктов со «случайным фото» выбирать в каждой проверке
CHECKLIST_RANDOM_PHOTOS = int(os.getenv("CHECKLIST_RANDOM_PHOTOS", 2))
# Если на пункт в среднем ушло меньше стольких секунд, проверка помечается «слишком быстро»
CHECKLIST_MIN_SECONDS_PER_ITEM = int(os.getenv("CHECKLIST_MIN_SECONDS_PER_ITEM", 4))


# --- Telegram --------------------------------------------------------------
# Токен бота выдаёт @BotFather. Пока токена нет, сообщения пишутся в лог.
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")


LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "loggers": {"otdoner": {"handlers": ["console"], "level": "INFO"}},
}
