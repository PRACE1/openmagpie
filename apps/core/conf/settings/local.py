import os

# Seed dev-only defaults BEFORE importing base.py, so its `os.environ[...]`
# reads resolve to localhost values when the env didn't set them. Keeps
# base.py honest (no localhost fallbacks baked into prod settings) while
# the dev loop still works out of the box.
os.environ.setdefault("BASE_URL", "http://localhost:8000")
os.environ.setdefault("WEB_BASE_URL", "http://localhost:3001")
# Browser ↔ Django is plain HTTP in dev; secure cookies would never get sent.
os.environ.setdefault("AUTH_COOKIE_SECURE", "false")

from .base import *  # noqa: E402, F403
from common.env import env_bool  # noqa: E402

DEBUG = env_bool("DEBUG", "true")
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "core", ".ngrok-free.app"]

# CORS: explicit allowlist even in dev. Allow-all + credentials would
# let any localhost page (a random dev server on :3000, a one-off
# tool's preview page) credentialed-fetch our /v1/auth/me and read
# session-bearing responses. Safe-method GETs aren't gated by our
# Origin-check CSRF (only non-safe methods are), so the allowlist is
# the only thing keeping cross-origin reads off the browser cookie.
CORS_ALLOWED_ORIGINS = [os.environ["WEB_BASE_URL"]]
CORS_ALLOW_CREDENTIALS = True
CSRF_TRUSTED_ORIGINS = [os.environ["WEB_BASE_URL"]]
