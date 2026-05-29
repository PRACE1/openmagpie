"""Connector registry. Maps kind string → Connector instance."""

from sources.connectors import Connector, RedditSubRedditConnector, RssConnector

_REGISTRY: dict[str, Connector] = {
    RedditSubRedditConnector.kind: RedditSubRedditConnector(),
    RssConnector.kind: RssConnector(),
}


def get(kind: str) -> Connector:
    """Raises KeyError if the kind has no registered connector."""
    return _REGISTRY[kind]


def register(connector: Connector) -> None:
    _REGISTRY[connector.kind] = connector
