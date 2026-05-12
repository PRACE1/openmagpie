from django.apps import AppConfig


class NotificationsConfig(AppConfig):
    name = "notifications"

    def ready(self) -> None:
        # Import the notifier registry so concrete notifiers register at startup.
        from notifications import registry  # noqa: F401
