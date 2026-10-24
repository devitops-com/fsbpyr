"""Settings of the corpus project; the database and static root come from the environment."""

import os
from pathlib import Path

SECRET_KEY = "corpus-not-secret"
DEBUG = False
ALLOWED_HOSTS = ["testserver"]
ROOT_URLCONF = "corpus_django.project.urls"
USE_TZ = True
USE_I18N = True
LANGUAGE_CODE = "en"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
STATIC_URL = "static/"
STATIC_ROOT = Path(os.environ["CORPUS_STATIC_ROOT"])

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corpus_django.notes",
]
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": [
        "django.template.context_processors.request",
        "django.contrib.auth.context_processors.auth",
        "django.contrib.messages.context_processors.messages",
    ]},
}]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3",
                         "NAME": os.environ["CORPUS_DB"]}}
REST_FRAMEWORK = {"DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
                  "DEFAULT_PERMISSION_CLASSES": [],
                  "UNAUTHENTICATED_USER": None}
