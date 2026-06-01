"""Shared wire primitives used across the response envelopes.

`ConfigBlob` is the opaque kind-specific `data` config carried on the
wire (e.g. a Feed's `data`). The server (Pydantic registry, keyed by
`kind`) is the sole validator; readers carry it verbatim and display via
a typed `summary` projection, never field-reading the blob. The envelope
around it IS typed (see `feed.py`).
"""

from typing import Any

ConfigBlob = dict[str, Any]
"""A kind-specific `data` config, opaque on the wire."""
