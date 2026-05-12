import os

from django.core.wsgi import get_wsgi_application
from dotenv import load_dotenv

load_dotenv()

env = os.environ.get("DJANGO_ENV", "dev")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", f"conf.settings.{env}")

application = get_wsgi_application()
