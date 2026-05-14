from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from events.observations import Observation
from listeners.configs import StreamSpec
from listeners.models import Listener


class ConnectorParseError(Exception):
    """A connector failed to parse a response from its upstream source.

    Raised when a 200 OK arrives with a body that isn't the shape we expect
    (HTML instead of JSON, payload schema change, missing required keys).
    Connectors should translate library-specific failures (json.JSONDecodeError,
    KeyError, TypeError on a dict walk, etc.) into this so the polling
    orchestrator can recover at the per-stream boundary without having to
    enumerate every parser library's exception types.
    """


class Connector(Protocol):
    """A pluggable source connector.

    Each implementation:
      - declares its `kind` (matches `StreamSpec.kind`),
      - declares its `observations` (Observation subclasses it produces, used
        by `events.registry` to hydrate `Event.data` back to typed Observations),
      - yields typed Observations for a given (stream_spec, listener) pair.
    """

    kind: str
    observations: list[type[Observation]]

    def poll(
        self,
        spec: StreamSpec,
        listener: Listener,
        since: datetime | None,
    ) -> Iterator[Observation]:
        """Yield typed Observations for one stream, newer than `since`."""
        ...

    def count(
        self,
        spec: StreamSpec,
        listener: Listener,
        since: datetime | None,
    ) -> int:
        """Exact count of observations newer than `since`. Used by the
        polling op's warm path to give progress UIs an `N/total` and
        an ETA.

        Cheapest universal implementation is `sum(1 for _ in self.poll(
        spec, listener, since=since))`. That walks the upstream pages
        a second time (so a warm cycle pays 2x bandwidth) but the
        Observation construction it does is microseconds, negligible
        next to per-observation LLM judging. Override if your upstream
        has a cheaper exact-count path.
        """
        ...
