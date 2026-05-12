import time

import httpx
from events.observations import Observation
from listeners.models import Listener
from pydantic import BaseModel

from .base import JudgmentJSON, JudgmentResult


class OllamaMessage(BaseModel):
    """One chat message in Ollama's /api/chat response envelope."""

    role: str
    content: str

    model_config = {"extra": "ignore"}


class OllamaChatResponse(BaseModel):
    """Ollama's /api/chat response shape. We only project the fields we read;
    Ollama emits a lot of others (model, created_at, done, eval_count, ...)
    that we don't need — extra='ignore' lets them pass through."""

    message: OllamaMessage

    model_config = {"extra": "ignore"}


SYSTEM_PROMPT = """You are a precise relevance scorer. Given a user's stated interest and an event observed from a stream (Reddit, GitHub, etc.), score how strongly the event matches that interest.

Respond with a JSON object matching this schema:
- score: float between 0.0 and 1.0 — relevance to the user's interest (0.0 = not relevant at all, 1.0 = an obvious, direct match)
- reason: short string under 200 characters explaining the score"""

USER_PROMPT_TEMPLATE = """User interest:
{listener_description}

Event:
  Source: {source}
  Title: {title}
  Content: {content}

Respond with JSON only."""

CONTENT_TRUNCATE = 2000


class OllamaEngine:
    """Calls a local Ollama server's /api/chat endpoint with structured JSON output."""

    kind = "ollama"

    def __init__(self, url: str, model: str) -> None:
        self.url = url.rstrip("/")
        self.model = model

    def judge(self, observation: Observation, listener: Listener) -> JudgmentResult:
        user_prompt = USER_PROMPT_TEMPLATE.format(
            listener_description=str(listener.description),
            source=observation.source,
            title=observation.title,
            content=observation.content[:CONTENT_TRUNCATE],
        )
        # `format` here is Ollama's structured-output knob: when given a JSON
        # schema, Ollama constrains the model's output to conform to it. That's
        # what makes message.content a JSON string matching JudgmentJSON below.
        # If a model/Ollama-version ever ignores it, JudgmentJSON.model_validate_json
        # raises ValidationError → caught by polling._RECOVERABLE_ERRORS.
        payload = {
            "model": self.model,
            "format": JudgmentJSON.model_json_schema(),
            "stream": False,
            # temperature=0 → greedy decoding. We want the same observation +
            # prompt to produce the same score across runs so the prompt itself
            # is what's under test, not LLM noise. (Tiny residual non-determinism
            # from inference parallelism is still possible but bounded.)
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        started = time.perf_counter()
        response = httpx.post(f"{self.url}/api/chat", json=payload, timeout=120.0)
        response.raise_for_status()
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        chat = OllamaChatResponse.model_validate(response.json())
        raw_content = chat.message.content
        parsed = JudgmentJSON.model_validate_json(raw_content)

        return JudgmentResult(
            score=parsed.score,
            reason=parsed.reason,
            model=self.model,
            latency_ms=elapsed_ms,
            raw_response=raw_content,
        )
