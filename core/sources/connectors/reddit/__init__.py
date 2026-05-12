"""Reddit connectors — public surface.

One file per concern:
  - `connector.py` — the Connector implementation(s) + polling logic
  - `payloads.py`  — Pydantic shapes for Reddit's wire format
  - `observations.py` — our internal Observation subclasses

Add new Reddit variants (user feed, search, comments, ...) as additional
connector classes alongside `RedditSubRedditConnector`.
"""

from .connector import RedditSubRedditConnector
from .observations import NewRedditPostObservation

__all__ = ["NewRedditPostObservation", "RedditSubRedditConnector"]
