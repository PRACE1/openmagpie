import os
from pathlib import Path

from common.env import env_bool

BASE_DIR = Path(__file__).resolve().parents[2]  # core/

SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]

DJANGO_ENV = os.environ.get("DJANGO_ENV", "local")
IS_PRODUCTION = DJANGO_ENV == "prod"

DJANGO_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "corsheaders",
    "oauth2_provider",
    "rest_framework",
]

# Single source of truth for our apps: drives INSTALLED_APPS *and*
# `_APP_LOGGERS` below. Add an app here once and it gets a logger config
# automatically, no second list to keep in sync.
LOCAL_APPS = [
    "common",
    "accounts",
    "auth_api",
    "sources",
    "feeds",
    "engine",
    "watches",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

AUTH_USER_MODEL = "accounts.User"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # CORS must come before CommonMiddleware so preflight responses get the
    # right headers regardless of other middleware short-circuiting.
    "corsheaders.middleware.CorsMiddleware",
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
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
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

BASE_URL = os.environ["BASE_URL"]

# Where the web app lives, used to build the browser-facing authorize URL
# the CLI prints during device flow. Required; dev.py supplies a localhost
# default so the dev loop works out of the box.
WEB_BASE_URL = os.environ["WEB_BASE_URL"]

# All HTTP API routes are mounted under /{API_VERSION_PREFIX}/. The web
# frontend reads the same value from NEXT_PUBLIC_API_VERSION, keep them in
# lockstep when bumping versions.
API_VERSION_PREFIX = os.environ.get("API_VERSION_PREFIX", "v1")

# CORS / CSRF for the web client. Empty by default so prod has to opt-in
# explicitly via env; `local.py` overrides with permissive dev defaults.
CORS_ALLOWED_ORIGINS = [o.strip() for o in os.environ.get("CORS_ALLOWED_ORIGINS", "").split(",") if o.strip()]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

# `auth_token` browser cookie (set by auth_api.cookies). Secure-by-default
# in base; local.py loosens it for plain-HTTP dev. Domain is unset (host-only)
# unless an explicit subdomain hand-off is needed.
AUTH_COOKIE_SECURE = env_bool("AUTH_COOKIE_SECURE", "true")
AUTH_COOKIE_DOMAIN = os.environ.get("AUTH_COOKIE_DOMAIN") or None

# OAuth Toolkit: minimal config, Bearer tokens via the password-equivalent
# path is handled by our own /v1/auth/login endpoint (which mints tokens via
# AccessToken/RefreshToken directly). The OAuth endpoints themselves are only
# used for refresh_token rotation, proxied through /v1/auth/refresh.
REST_FRAMEWORK = {
    # Our custom auth (Bearer OR auth_token cookie) is the only auth scheme
    # in v0; permission gating is per-view via `IsAuthenticated`. No DRF
    # session auth, so CSRF is not enforced on our APIViews.
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "auth_api.authentication.BearerOrCookieAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [],
    "UNAUTHENTICATED_USER": None,
    # Clears the stale auth_token cookie on AuthenticationFailed so
    # browsers stop sending invalid credentials on every subsequent
    # request after a server-side revoke / expiry.
    "EXCEPTION_HANDLER": "auth_api.exception_handlers.auth_aware_exception_handler",
}

OAUTH2_PROVIDER = {
    "ACCESS_TOKEN_EXPIRES_SECONDS": int(os.environ.get("ACCESS_TOKEN_EXPIRES_SECONDS", "3600")),
    "REFRESH_TOKEN_EXPIRE_SECONDS": int(os.environ.get("REFRESH_TOKEN_EXPIRE_SECONDS", str(14 * 86400))),
    "ROTATE_REFRESH_TOKEN": True,
    "SCOPES": {"read": "Read", "write": "Read and write"},
    "DEFAULT_SCOPES": ["read", "write"],
    "PKCE_REQUIRED": False,
}

# Device session (CLI login handshake) TTLs. Short, because the user is
# expected to authorize within minutes; the completed bag is even shorter
# since the CLI polls every 2s and needs only a brief window to pick it up.
DEVICE_SESSION_PENDING_TTL_SECONDS = int(os.environ.get("DEVICE_SESSION_PENDING_TTL_SECONDS", str(15 * 60)))
DEVICE_SESSION_COMPLETED_TTL_SECONDS = int(os.environ.get("DEVICE_SESSION_COMPLETED_TTL_SECONDS", str(5 * 60)))

# Cache (also used as the lock backend for scheduler concurrency control).
# Defaults to Django's db cache, zero-deps, survives process restart, fine
# for single-host. Swap to django-redis / memcached via env when there's a
# real reason (multi-host scheduler, ephemeral locks). Run
# `manage.py createcachetable` once after switching backends or on cold db.
CACHE_BACKEND = os.environ.get("CACHE_BACKEND", "django.core.cache.backends.db.DatabaseCache")
CACHE_LOCATION = os.environ.get("CACHE_LOCATION", "openmagpie_cache")
CACHES = {"default": {"BACKEND": CACHE_BACKEND, "LOCATION": CACHE_LOCATION}}

# Scheduler-lock failsafe TTL, only kicks in if a process holds the lock
# longer than the cache key's expiry, in which case it auto-releases. The
# feed poll cycle is dominated by per-item LLM calls downstream, so the
# ceiling is set well above the longest healthy cycle but tight enough
# that a stuck process unblocks soonish.
POLL_LOCK_TIMEOUT_SECONDS = int(os.environ.get("POLL_LOCK_TIMEOUT_SECONDS", "600"))

# Feed set-sources serialization lock. The critical section is the
# diff + bulk insert/delete + per-row meta updates ; bounded by the
# count of sources but not by network. 60s is well above a 1k-row
# reconcile and tight enough that a stuck CLI run unblocks.
FEED_SET_LOCK_TIMEOUT_SECONDS = int(os.environ.get("FEED_SET_LOCK_TIMEOUT_SECONDS", "60"))

# Watch-path chain-mutation lock. The critical section is a snapshot +
# dense-rank renumber + bulk_update over a path's actions (~2-10 rows),
# no network. 30s is far above a real renumber and unblocks a stuck run.
PATH_CHAIN_LOCK_TIMEOUT_SECONDS = int(os.environ.get("PATH_CHAIN_LOCK_TIMEOUT_SECONDS", "30"))

# Refresh-token rotation lock failsafe. The critical section is one row
# read + revoke + mint, so milliseconds in practice; 30s leaves plenty
# of headroom if the DB is briefly slow.
REFRESH_TOKEN_LOCK_TIMEOUT_SECONDS = int(os.environ.get("REFRESH_TOKEN_LOCK_TIMEOUT_SECONDS", "30"))

# Relevance engine defaults. ENGINE_DEFAULT_KIND selects which engine a
# semantic-filter action's config falls back to when it doesn't pin one
# explicitly. The kind must be registered in `engine.registry`.
ENGINE_DEFAULT_KIND = os.environ.get("ENGINE_DEFAULT_KIND", "ollama")

# Ollama (relevance engine). Required when ENGINE_DEFAULT_KIND="ollama", fail
# fast at startup if unset. `OLLAMA_DEFAULT_MODEL` is the fallback when an
# action's config leaves `engine.model` empty; named "default" (not
# "model") so it doesn't read like "the" model when the system supports
# per-action overrides. See core/.env.example for example values.
OLLAMA_URL = os.environ["OLLAMA_URL"]
OLLAMA_DEFAULT_MODEL = os.environ["OLLAMA_DEFAULT_MODEL"]

# Webhook-action security gates (consumed by the WebhookAction when it
# lands). Defaults assume single-tenant self-host with possible internal
# targets. Set stricter values via env for multi-tenant / public deployments.
WEBHOOK_REQUIRE_HTTPS = env_bool("WEBHOOK_REQUIRE_HTTPS", "false")
WEBHOOK_BLOCK_PRIVATE_IPS = env_bool("WEBHOOK_BLOCK_PRIVATE_IPS", "false")

# Source-fetch SSRF gate. Parallel to WEBHOOK_BLOCK_PRIVATE_IPS but for
# OUTBOUND-from-connector fetches (RSS, future HTTP-based connectors).
# When true, a Source create / set with an IP-literal URL pointing at a
# private / loopback / link-local / multicast / reserved address is
# rejected at the policy seam (PolicyError -> 400), and the connector
# additionally re-resolves on every outbound request (including
# redirects) so a public hostname that resolves to a private address,
# or a 302 to an internal target, is rejected at poll time as a
# ConnectorParseError. Default off matches the webhook policy posture:
# single-tenant self-host where the operator might legitimately watch
# an internal feed; flip on for multi-tenant / public deployments.
SOURCE_BLOCK_PRIVATE_IPS = env_bool("SOURCE_BLOCK_PRIVATE_IPS", "false")

# Anti-bot challenge-bypass sidecar (shared connector primitive).
# Bot-management products flag Python TLS fingerprints and serve a JS-
# challenge page (HTTP 202, or 200-with-bot-HTML) in place of the real
# payload. When set, any connector mixing in `ChallengeBypassMixin`
# POSTs the failing URL to a FlareSolverr sidecar
# (https://github.com/FlareSolverr/FlareSolverr) which runs headless
# Chrome in its own container, passes the JS challenge, and returns
# the real body. Empty default (no localhost / docker fallback baked
# into prod settings) ; dev seeds the docker-compose service name in
# `conf.settings.local`.
SOURCE_CHALLENGE_BYPASS_URL = os.environ.get("SOURCE_CHALLENGE_BYPASS_URL", "")

# Allow the RSS connector to retry a failing fetch with TLS chain
# verification disabled. Some publisher feeds sit behind stale / self-
# signed / wrong-name certs and serve valid bodies once verification
# is dropped, BUT verify=False is a real MITM downgrade for feed
# content (anyone on-path can forge entries). Default off ; turn on
# only for single-tenant self-host where the operator accepts the
# trade-off.
SOURCE_ALLOW_INSECURE_TLS = env_bool("SOURCE_ALLOW_INSECURE_TLS", "false")

# ── Logging ────────────────────────────────────────────────────────────
#
# App loggers are configured explicitly so the warnings the polling /
# delivery / engine code emits surface predictably regardless of
# Python's default lastResort handler quirks. App level is INFO by
# default; per-logger override via the `LOG_LEVEL_<APP>` env so an
# operator can crank `LOG_LEVEL_FEEDS=DEBUG` without editing code.
#
# Django's own loggers are left at framework defaults via
# `disable_existing_loggers=False`.

LOG_LEVEL_APP = os.environ.get("LOG_LEVEL_APP", "INFO").upper()


def _app_log_level(app: str) -> str:
    key = f"LOG_LEVEL_{app.upper()}"
    return os.environ.get(key, LOG_LEVEL_APP).upper()


# Derived from LOCAL_APPS so a new app can't ship without its logger
# config (the drift this avoids).
_APP_LOGGERS = tuple(LOCAL_APPS)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "concise": {
            "format": "{asctime} {levelname:<7} {name}: {message}",
            "style": "{",
            "datefmt": "%H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "concise",
            # stdout so application logs interleave cleanly with
            # management-command output and `docker compose logs` (and
            # `tee` capture). Operational errors still get emitted via
            # logger.warning/.error (same handler, same stdout); we
            # don't split INFO/ERROR across streams in v0.
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        app: {
            "handlers": ["console"],
            "level": _app_log_level(app),
            "propagate": False,
        }
        for app in _APP_LOGGERS
    },
}
