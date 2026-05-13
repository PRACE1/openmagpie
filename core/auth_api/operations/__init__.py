"""One-shot orchestrators for the auth flow.

Per AGENTS.md: Operations are single-use orchestrators (`.run()` once,
discard) that compose multiple services. Service classes stay account-
scoped and reusable; Operations capture the cross-app dance for a
specific user-facing action (signup, future password reset, etc.).
"""

from .signup import EmailAlreadyExists, SignupOperation

__all__ = ["EmailAlreadyExists", "SignupOperation"]
