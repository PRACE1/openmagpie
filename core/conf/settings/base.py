import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]  # core/

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DJANGO_ENV = os.environ.get("DJANGO_ENV", "dev")
IS_PRODUCTION = DJANGO_ENV == "prod"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Local apps
    "common",
    "accounts",
    "events",
    "sources",
    "listeners",
    "engine",
    "notifications",
]

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "conf.urls"

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

WSGI_APPLICATION = "conf.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

BASE_URL = os.environ.get("BASE_URL", "http://localhost:8000")

# Cache (also used as the lock backend for scheduler concurrency control).
# Defaults to Django's db cache — zero-deps, survives process restart, fine
# for single-host. Swap to django-redis / memcached via env when there's a
# real reason (multi-host scheduler, ephemeral locks). Run
# `manage.py createcachetable` once after switching backends or on cold db.
CACHE_BACKEND = os.environ.get(
    "CACHE_BACKEND", "django.core.cache.backends.db.DatabaseCache"
)
CACHE_LOCATION = os.environ.get("CACHE_LOCATION", "openmagpie_cache")
CACHES = {"default": {"BACKEND": CACHE_BACKEND, "LOCATION": CACHE_LOCATION}}

# Scheduler-lock failsafe TTLs — only kicks in if a process holds the lock
# longer than the cache key's expiry, in which case it auto-releases. Each
# scope gets its own ceiling because realistic cycle durations differ a lot:
# polls are dominated by per-observation LLM calls (slow), digests just fire
# already-batched payloads (fast). Both should be set well above the longest
# healthy cycle but tight enough that a stuck process unblocks soonish.
POLL_LOCK_TIMEOUT_SECONDS = int(os.environ.get("POLL_LOCK_TIMEOUT_SECONDS", "600"))
DIGEST_LOCK_TIMEOUT_SECONDS = int(os.environ.get("DIGEST_LOCK_TIMEOUT_SECONDS", "120"))

# Relevance engine defaults. ENGINE_DEFAULT_KIND selects which engine
# `SemanticListenerConfig` falls back to when a Listener's config doesn't
# pin one explicitly. The kind must be registered in `engine.registry`.
ENGINE_DEFAULT_KIND = os.environ.get("ENGINE_DEFAULT_KIND", "ollama")

# Ollama (relevance engine). Required when ENGINE_DEFAULT_KIND="ollama" — fail
# fast at startup if unset. See core/.env.example for example values.
OLLAMA_URL = os.environ["OLLAMA_URL"]
OLLAMA_MODEL = os.environ["OLLAMA_MODEL"]

# WebhookNotifier security gates. Defaults assume single-tenant self-host with
# possible internal targets (e.g. an OpenClaw instance on the same box). Set
# stricter values via env for multi-tenant / public deployments.
WEBHOOK_REQUIRE_HTTPS = (
    os.environ.get("WEBHOOK_REQUIRE_HTTPS", "false").lower() == "true"
)
WEBHOOK_BLOCK_PRIVATE_IPS = (
    os.environ.get("WEBHOOK_BLOCK_PRIVATE_IPS", "false").lower() == "true"
)
