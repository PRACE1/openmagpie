from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, Field

from events.observations import Observation
from listeners.models import Listener


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
    the *hit* decision is made by the caller against a Listener-configured
    threshold (`SemanticListenerConfig.hit_threshold`)."""

    score: float
    reason: str
    model: str
    latency_ms: int
    raw_response: str


class Engine(Protocol):
    """A pluggable relevance engine. Implementations live in this package."""

    kind: str

    def judge(self, observation: Observation, listener: Listener) -> JudgmentResult:
        """Score how relevant the observation is to the listener's interest."""
        ...
