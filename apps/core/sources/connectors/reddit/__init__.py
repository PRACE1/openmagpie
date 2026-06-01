"""Reddit connectors, public surface.

One file per concern:
  - `connector.py`, the Connector implementation(s) + polling logic
  - `payloads.py`, our internal SourcePayload subclasses
    (feedparser owns the raw-XML projection)

Add new Reddit variants (user feed, search, comments, ...) as additional
connector classes alongside `RedditSubRedditConnector`.
"""

from .connector import RedditSubRedditConnector
from .payloads import NewRedditPostPayload

__all__ = ["NewRedditPostPayload", "RedditSubRedditConnector"]
