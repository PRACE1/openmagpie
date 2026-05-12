import os

from .base import *  # noqa: F403

DEBUG = os.environ.get("DEBUG", "true").lower() == "true"
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "core", ".ngrok-free.app"]
