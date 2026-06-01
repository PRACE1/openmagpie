"""Per-kind WatchAction config + result contracts (the tight shapes
behind the opaque `config` / `result` blobs on WatchAction / WatchActionRun).

SHARED, zero-Django source of truth. The DB columns are opaque
(`ConfigBlob` / `ResultBlob`) ; these are the strict models the server
validates a `config` against at the API write boundary, and the strict
`result` the runner writes when it persists a run. Settings-coupled
policy (engine kind registered, threshold bounds beyond the structural
gt/le, SSRF on future webhook URLs) lives server-side, not here.

`kind` is NOT a field on these configs ; it lives one level up (the
WatchAction.kind column + the write envelope's `kind`), and the server's
`watches.registry` maps `kind -> config class` to validate the blob. So
the persisted `config` is the PURE kind-specific shape, no discriminator
nested inside it. Mirrors how `feeds` keeps `Feed.kind` off `Feed.data`.

v1 ships the FILTER family (semantic_filter). The DELIVERY family
(webhook, log) lands with its implementations in a later commit ; each
adds its config + result class and a registry entry. Adding / removing a
kind is a pure-Python change (no `choices=` on the column, no migration).
"""

from typing import Any, ClassVar

from pydantic import BaseModel, Field


class EngineSpec(BaseModel):
    """Which engine + model a semantic filter uses to score relevance.

    `kind == ""` means "use the server default" ; the server fills it
    from settings and rejects an unregistered kind (policy ; the pure
    package can't know the registry). `model` is informational.
    """

    kind: str = ""
    model: str = ""


class WatchActionConfigSummary(BaseModel):
    """Display-only projection of an action config for the CLI preview.

    Built server-side from the typed config (the only place that knows
    the schema), so the CLI prints it without parsing the `config` blob ;
    no shadow schema on the client. `detail` is presentation, not a
    contract ; the action's `kind` is carried separately (the column /
    wire), so it isn't repeated here."""

    detail: str = ""


class WatchActionConfigBase(BaseModel):
    """Base for every action-kind config.

    Declares the read-path contract every kind MUST implement. No working
    defaults: a silent `summary()` shows a blank preview and a default
    `redacted_dump()` would leak a future kind's secrets. Fail loudly
    here, don't ship a silent hole. Mirrors `FeedConfig`.

    No `kind` field: the discriminator lives on the WatchAction row /
    write envelope, and the registry maps it to the right subclass. The
    concrete config is the pure kind-specific shape."""

    def redacted_dump(self) -> dict[str, Any]:
        raise NotImplementedError(
            f"{type(self).__name__} must implement redacted_dump() (no safe default: the fallback would leak secrets)"
        )

    def summary(self) -> WatchActionConfigSummary:
        raise NotImplementedError(f"{type(self).__name__} must implement summary()")

    def merge_preserving(self, prior: "WatchActionConfigBase") -> "WatchActionConfigBase":
        """Edit round-trip: return self with state that must NOT reset on
        an edit, carried from `prior` (e.g. a future webhook's masked
        secret). No safe default: a silent passthrough would corrupt
        secrets to the redaction sentinel."""
        raise NotImplementedError(
            f"{type(self).__name__} must implement merge_preserving() (no safe default: would corrupt secrets)"
        )


class SemanticFilterConfig(WatchActionConfigBase):
    """Config for a WatchAction with kind == 'semantic_filter'.

    The LLM relevance gate: scores each item against `instructions` with
    `engine`, and the run GATES the chain when the score is below
    `threshold` (a pass=false). It is the v2 home for what the old
    Listener carried as instructions + engine + hit_threshold ; it owns
    no feed (the Watch subscribes to feeds) and no delivery (that's the
    delivery actions)."""

    CONFIG_KIND: ClassVar[str] = "semantic_filter"

    # What the engine scores items against (required ; an empty filter
    # would pass everything and defeat the purpose).
    instructions: str
    # default = EngineSpec(kind=""); the server fills the real default
    # kind from settings + validates it (policy).
    engine: EngineSpec = Field(default_factory=EngineSpec)
    # The run passes (advances the chain) when score >= threshold, else
    # GATES. Strict `gt=0.0` so a 0 threshold can't pass every item ; an
    # engine returning 0 for "irrelevant" would otherwise never gate.
    threshold: float = Field(default=0.8, gt=0.0, le=1.0)

    model_config = {"extra": "ignore"}

    def redacted_dump(self) -> dict[str, Any]:
        """No secrets in a semantic filter (instructions / engine /
        threshold are all non-secret), so a plain dump is safe. The
        contract is here so a future secret-bearing kind can't ship
        without implementing it."""
        return self.model_dump(mode="json")

    def summary(self) -> WatchActionConfigSummary:
        # engine.kind == "" is the documented "use server default" ; render
        # a placeholder rather than an empty token so the preview reads
        # (e.g. "engine(default) >= 0.80", not a bare ">= 0.80").
        kind = self.engine.kind or "default"
        engine = f"{kind} | {self.engine.model}" if self.engine.model else f"engine({kind})"
        return WatchActionConfigSummary(detail=f"{engine} >= {self.threshold:.2f}")

    def merge_preserving(self, prior: "WatchActionConfigBase") -> "SemanticFilterConfig":
        """Nothing to carry forward: a semantic filter has no masked
        secrets or runtime state, so the submitted config wins wholesale."""
        return self


class SemanticFilterResult(BaseModel):
    """Result a semantic-filter run writes to WatchActionRun.result.

    `passed` is the gate decision (False -> the run is GATED, the chain
    stops) ; `score` is the engine's relevance in [0.0, 1.0] (kept for the
    audit log + threshold tuning), bounded to match the engine contract +
    `SemanticFilterConfig.threshold` so an out-of-range score is rejected
    at the write boundary, not silently logged. `passed` not `pass`
    because `pass` is a Python keyword."""

    passed: bool
    score: float = Field(ge=0.0, le=1.0)
    reason: str = ""
