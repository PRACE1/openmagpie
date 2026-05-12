import os

from django.core.asgi import get_asgi_application
from dotenv import load_dotenv

load_dotenv()

env = os.environ.get("DJANGO_ENV", "dev")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"conf.settings.{env}")

application = get_asgi_application()
