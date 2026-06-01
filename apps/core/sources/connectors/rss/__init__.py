"""Generic RSS / Atom connector, public surface.

One file per concern, mirroring the reddit subpackage layout:
  - `connector.py` ; the `RssConnector` impl + polling logic
  - `payloads.py` ; `RssEntryPayload` + the per-field precedence chain
    used by the `field_map` override

feedparser owns the raw-XML parse ; its FeedParserDict (a hybrid dict /
attr-access object) is the only shape this connector reads. `payloads.py`
projects that into our typed `RssEntryPayload`.
"""

from .connector import RssConnector
from .payloads import RssEntryPayload

__all__ = ["RssConnector", "RssEntryPayload"]
