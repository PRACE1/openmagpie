"""Re-export of the shared, pure config models.

The models now live ONCE in the `openmagpie-schema` workspace package
(imported by both core and the magpie CLI - no hand-copying across the
boundary). This module stays as the stable in-core import path
(`from listeners.configs import ...`) so the rest of core is untouched.

Django/settings-coupled *policy* (engine-kind registered, no future
watermark, webhook SSRF/https, default engine kind) is NOT here - it
lives in `listeners.policy` and runs at the validation seam. Splitting
shape (shared) from policy (server) is what makes the package
dependency-free.
"""

from openmagpie_schema.configs import (
    REDACTED,
    EngineSpec,
    ListenerConfig,
    ListenerConfigSummary,
    LogNotifierSpec,
    NotifierSpec,
    NotifierSpecBase,
    RedditSubredditStreamSpec,
    SemanticListenerConfig,
    StreamSpec,
    StreamWatch,
    WebhookNotifierSpec,
)

__all__ = [
    "REDACTED",
    "EngineSpec",
    "ListenerConfig",
    "ListenerConfigSummary",
    "LogNotifierSpec",
    "NotifierSpec",
    "NotifierSpecBase",
    "RedditSubredditStreamSpec",
    "SemanticListenerConfig",
    "StreamSpec",
    "StreamWatch",
    "WebhookNotifierSpec",
]
