"""OllamaEngine: calls a local Ollama server's /api/chat for relevance
scoring, with structured JSON output constrained by JudgmentJSON's
schema. Also exposes /api/tags for the listener-config policy check
that verifies a pinned `engine.model` is actually loaded.
"""

import time

import httpx
from pydantic import ValidationError

from events.observations import Observation
from listeners.models import Listener
from openmagpie_schema.engine import EngineStatus

from ..base import EngineModelInvalid, JudgmentJSON, JudgmentResult
from .prompts import CONTENT_TRUNCATE, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .responses import OllamaChatResponse, OllamaTagsResponse


class OllamaEngine:
    """Calls a local Ollama server's /api/chat endpoint with structured JSON output."""

    kind = "ollama"

    def __init__(self, url: str, default_model: str) -> None:
        self.url = url.rstrip("/")
        # `default_model` is the fallback when a Listener's config leaves
        # `engine.model` empty. Named "default_model" (not "model") because
        # judge() also takes a per-call `model` override; calling the instance
        # attribute `self.model` made `model or self.model` read ambiguously.
        self.default_model = default_model

    def judge(
        self,
        observation: Observation,
        listener: Listener,
        *,
        model: str | None = None,
    ) -> JudgmentResult:
        # Per-call model override: listener's `SemanticListenerConfig.engine.model`
        # threads through judgment.py; None means "use this OllamaEngine
        # instance's default" (settings.OLLAMA_DEFAULT_MODEL from env).
        use_model = model or self.default_model
        user_prompt = USER_PROMPT_TEMPLATE.format(
            listener_instructions=str(listener.instructions),
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
            "model": use_model,
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
            model=use_model,
            latency_ms=elapsed_ms,
            raw_response=raw_content,
        )

    def available_models(self) -> list[str]:
        """Names of models currently loaded on this Ollama server.

        Raises httpx.HTTPError on unreachable server and
        pydantic.ValidationError if Ollama's tags-response shape ever
        drifts (e.g. renamed `models` field). Both bubble up; the
        listener-config policy callsite wraps them in
        EngineModelInvalid with operator-facing detail.
        """
        response = httpx.get(f"{self.url}/api/tags", timeout=5.0)
        response.raise_for_status()
        tags = OllamaTagsResponse.model_validate_json(response.text)
        return [m.name for m in tags.models]

    def status(self) -> EngineStatus:
        """Probe /api/tags once; map success or any failure into an
        `EngineStatus`. Never raises — callers (the /v1/engines view,
        the quickstart wizard) render `unreachable_reason` directly,
        so a probe error here is not the same as a server error."""
        try:
            loaded = self.available_models()
        except httpx.HTTPError as exc:
            return EngineStatus(
                kind=self.kind,
                default_model=self.default_model,
                available=False,
                unreachable_reason=f"Ollama at {self.url} unreachable ({type(exc).__name__}: {exc})",
            )
        except ValidationError as exc:
            return EngineStatus(
                kind=self.kind,
                default_model=self.default_model,
                available=False,
                unreachable_reason=f"Ollama at {self.url} returned an unexpected /api/tags shape ({exc.error_count()} error(s))",
            )
        return EngineStatus(
            kind=self.kind,
            default_model=self.default_model,
            available=True,
            available_models=sorted(loaded),
        )

    def validate_model(self, model: str) -> None:
        """Confirm `model` is loaded on this Ollama server. Engine-policy
        hook called from listener config save (see Engine protocol).
        Raises EngineModelInvalid when the server is unreachable OR the
        model isn't in the loaded set — message names the URL and the
        available list so the operator can fix the YAML or pull the
        model on the Ollama side.
        """
        try:
            loaded = self.available_models()
        except httpx.HTTPError as exc:
            raise EngineModelInvalid(f"can't validate engine.model: Ollama at {self.url} unreachable ({exc})") from exc
        if model not in loaded:
            raise EngineModelInvalid(
                f"engine.model {model!r} not loaded on Ollama at {self.url}; available: {sorted(loaded) or '(none)'}"
            )
