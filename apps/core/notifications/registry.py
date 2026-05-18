"""Notifier registry. Maps kind string → Notifier instance."""

from notifications.notifiers import LogNotifier, Notifier, WebhookNotifier

_REGISTRY: dict[str, Notifier] = {
    LogNotifier.kind: LogNotifier(),
    WebhookNotifier.kind: WebhookNotifier(),
}


def get(kind: str) -> Notifier:
    """Raises KeyError if the kind has no registered notifier."""
    return _REGISTRY[kind]


def register(notifier: Notifier) -> None:
    _REGISTRY[notifier.kind] = notifier
