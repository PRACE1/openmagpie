"""Typed response models for the Ollama HTTP API endpoints we consume.

Each response is validated through Pydantic so a schema drift on
Ollama's side (renamed envelope field, removed `name`, etc.) raises a
named ValidationError at the use site instead of a silent KeyError or
empty-list false-negative. `extra='ignore'` on every model so the
fields we don't read (eval_count, modified_at, digest, ...) pass through
unchanged.
"""

from pydantic import BaseModel


class OllamaMessage(BaseModel):
    """One chat message in Ollama's /api/chat response envelope."""

    role: str
    content: str

    model_config = {"extra": "ignore"}


class OllamaChatResponse(BaseModel):
    """Ollama's /api/chat response shape. We only project the fields we
    read; Ollama emits a lot of others (model, created_at, done,
    eval_count, ...) that we don't need."""

    message: OllamaMessage

    model_config = {"extra": "ignore"}


class OllamaModelTag(BaseModel):
    """One entry in Ollama's /api/tags `models` array. Only `name` is
    read; `model`, `modified_at`, `size`, `digest`, `details` pass
    through under extra='ignore'."""

    name: str

    model_config = {"extra": "ignore"}


class OllamaTagsResponse(BaseModel):
    """Ollama's /api/tags response envelope (`{"models": [...]}`).
    Validates the shape so a future Ollama API change (e.g. renamed
    `models` field) fails loud at save time with a ValidationError
    instead of an empty `available_models()` list silently rejecting
    every operator's model choice."""

    models: list[OllamaModelTag] = []

    model_config = {"extra": "ignore"}
