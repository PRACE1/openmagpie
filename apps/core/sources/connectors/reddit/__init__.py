"""Reddit connectors, public surface.

One file per concern:
  - `connector.py`, the Connector implementation(s) + polling logic
  - `observations.py`, our internal Observation subclasses
    (feedparser owns the raw-XML projection so there's no payloads.py)

Add new Reddit variants (user feed, search, comments, ...) as additional
connector classes alongside `RedditSubRedditConnector`.
"""

from .connector import RedditSubRedditConnector
from .observations import NewRedditPostObservation

__all__ = ["NewRedditPostObservation", "RedditSubRedditConnector"]
