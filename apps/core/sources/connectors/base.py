from collections.abc import Iterator
from datetime import datetime
from typing import Protocol

from events.observations import Observation
from openmagpie_schema.configs import SourceSpec


class ConnectorParseError(Exception):
    """A connector failed to parse a response from its upstream source.

    Raised when a 200 OK arrives with a body that isn't the shape we expect
    (HTML instead of JSON, payload schema change, missing required keys).
    Connectors should translate library-specific failures (json.JSONDecodeError,
    KeyError, TypeError on a dict walk, etc.) into this so the polling
    orchestrator can recover at the per-source boundary without having to
    enumerate every parser library's exception types.
    """


class Connector(Protocol):
    """A pluggable source connector.

    Each implementation:
      - declares its `kind` (matches `SourceSpec.kind`),
      - declares its `observations` (Observation subclasses it produces, used
        by `events.registry` to hydrate stored data back to typed Observations),
      - yields typed Observations for a given stream_spec.

    Connectors are tenant-agnostic: the Feed drives polling, so `poll` takes
    only the source spec + watermark (no listener/account).
    """

    kind: str
    observations: list[type[Observation]]

    def poll(
        self,
        spec: SourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
    ) -> Iterator[Observation]:
        """Yield typed Observations for one source, newer than `since`.

        `field_map` is the EFFECTIVE map for this source ; the polling
        orchestrator merges the feed's `default_field_map` with the
        Source row's `field_map` (row wins per key) and passes the
        result. Connectors that don't read it (e.g. Reddit) accept and
        ignore. Recognized keys are per-connector; unknown keys are
        silently dropped. None == empty dict."""
        ...

    def count(
        self,
        spec: SourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
    ) -> int:
        """Exact count of observations newer than `since`. Used by the
        polling op's warm path to give progress UIs an `N/total` and
        an ETA.

        This is a structural signature only. There is no free default
        from this Protocol; inherit `BaseConnector` to get the universal
        poll-walk implementation, or implement `count` yourself.
        """
        ...


class BaseConnector:
    """Concrete base supplying the universal `count` implementation.

    Inherit this and a new connector gets a correct `count` for free:
    it re-walks `poll` and discards each Observation. That doubles the
    upstream bandwidth for a warm cycle, but the Observation construction
    is microseconds, negligible next to per-observation LLM judging.
    Override `count` only if your upstream has a cheaper exact-count path.

    `BaseConnector` itself is NOT a `Connector` (it has no `kind` /
    `observations`); a concrete subclass that declares those plus `poll`
    is what structurally satisfies the Protocol. This class only supplies
    the `count` default, it is opt-in, not a required parent.
    """

    def count(
        self,
        spec: SourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
    ) -> int:
        return sum(1 for _ in self.poll(spec, since=since, field_map=field_map))

    def poll(
        self,
        spec: SourceSpec,
        since: datetime | None,
        field_map: dict[str, str] | None = None,
    ) -> Iterator[Observation]:  # pragma: no cover - subclass responsibility
        raise NotImplementedError
