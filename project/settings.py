"""
Standalone TimeSpace settings.
Run with: python manage.py runserver
"""
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = "timespace-dev-key-change-in-production"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "timespace",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "project.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": False,
        "OPTIONS": {
            "loaders": [
                "django.template.loaders.filesystem.Loader",
                "django.template.loaders.app_directories.Loader",
            ],
            "context_processors": [
                "django.template.context_processors.request",
                "timespace.context_processors.base_template",
            ],
        },
    },
]

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

# TimeSpace config
TIMESPACE_STANDALONE = True
TIMESPACE_EVENTS_DIR = BASE_DIR / "events"
TIMESPACE_POIS_DIR = BASE_DIR / "pois"

# Optional: point at a W66 content checkout to resolve W66 POI paths
# WORLD66_CONTENT_DIR = "/path/to/world66/content"
