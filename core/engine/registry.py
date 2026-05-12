"""Engine registry. Maps kind string → Engine instance, configured from Django settings."""

from django.conf import settings

from .engines import Engine, OllamaEngine

_REGISTRY: dict[str, Engine] = {
    OllamaEngine.kind: OllamaEngine(
        url=settings.OLLAMA_URL,
        model=settings.OLLAMA_MODEL,
    ),
}


def get(kind: str) -> Engine:
    """Raises KeyError if the kind has no registered engine."""
    return _REGISTRY[kind]


def register(engine: Engine) -> None:
    _REGISTRY[engine.kind] = engine
