from django.apps import AppConfig


class SourcesConfig(AppConfig):
    name = "sources"

    def ready(self) -> None:
        # Import connectors so they register their Observation classes
        # with events.registry at startup.
        from sources import registry  # noqa: F401
