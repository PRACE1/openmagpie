"""Generic RSS / Atom connector, public surface.

One file per concern, mirroring the reddit subpackage layout:
  - `connector.py` ; the `RssConnector` impl + polling logic
  - `observations.py` ; `RssEntryObservation` + the per-field
    precedence chain used by the `field_map` override

No `payloads.py` ; feedparser owns the parse, and its
FeedParserDict (a hybrid dict / attr-access object) is the only
shape this connector reads. Projecting it into a typed Pydantic
model would just shadow feedparser's already-normalized keys
without adding safety.
"""

from .connector import RssConnector
from .observations import RssEntryObservation

__all__ = ["RssConnector", "RssEntryObservation"]
