"""Ollama engine package.

Public surface: `OllamaEngine`. Everything else (typed response models,
prompts, content-size constants) is package-internal — split out so a
prompt iteration or a response-schema drift diff is a 1-file change in
review, not buried in the engine class's history.
"""

from .engine import OllamaEngine

__all__ = ["OllamaEngine"]
