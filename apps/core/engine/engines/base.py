from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from openmagpie_schema.engine import EngineStatus
from sources.payloads import SourcePayload


class JudgmentJSON(BaseModel):
    """LLM-output contract for any score-shaped relevance engine.

    Doubles as the structured-output schema we hand to the model (via e.g.
    Ollama's `format` field, OpenAI's `response_format`, etc.) and as the
    parser on the way back. Engine implementations are free to use it as-is
    or define their own, but the shape they map into `JudgmentResult` is
    this one.
    """

    score: float = Field(ge=0.0, le=1.0)
    reason: str


@dataclass(frozen=True)
class JudgmentResult:
    """In-memory verdict from an Engine. The engine scores relevance (0.0-1.0);
    the pass / fail decision is made by the caller against a configured
    threshold (a semantic-filter action's `hit_threshold`)."""

    score: float
    reason: str
    model: str
    latency_ms: int
    raw_response: str


class EngineModelInvalid(ValueError):
    """The model a caller pinned can't be used by this engine instance ;
    not loaded on the upstream server, doesn't match the provider's name
    pattern, etc. Raised by `Engine.validate_model`; the caller's config
    policy translates it to a PolicyError at the save boundary.

    Lives in the engine layer because the engine knows the failure modes;
    the calling layer just runs the check and maps the result to
    HTTP-shaped errors."""


class Engine(Protocol):
    """A pluggable relevance engine. Implementations live in this package."""

    kind: str

    def judge(
        self,
        payload: SourcePayload,
        *,
        instructions: str,
        model: str | None = None,
    ) -> JudgmentResult:
        """Score how relevant the payload is to the caller's `instructions`.

        `model` lets the caller override the engine's default model on a
        per-call basis (so a pinned `engine.model` isn't a no-op). None
        means "use the engine instance's configured default."
        """
        ...

    def validate_model(self, model: str) -> None:
        """Confirm `model` is usable by this engine instance. Called by
        the caller's config policy at save time when a non-empty
        `engine.model` is pinned, so the operator finds out at create/
        update if their choice can't be served (vs every judge cycle
        500ing).

        Raises `EngineModelInvalid` if the model can't be used. Engines
        with no meaningful per-model check (e.g. providers whose model
        choice can't be pre-verified without sending real traffic)
        implement this as a no-op `pass` — explicit, not hasattr-checked.
        """
        ...

    def status(self) -> EngineStatus:
        """Reachability snapshot for `/v1/engines` and pre-flight UIs.

        Implementations probe the upstream (or whatever counts as
        "reachable" for the provider) and return a populated
        `EngineStatus`. Never raises: unreachable / shape-drift errors
        must be reported as `available=False` with an operator-facing
        `unreachable_reason` so a CLI consumer can render it directly.
        """
        ...
