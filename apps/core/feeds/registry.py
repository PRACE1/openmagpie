"""Feed kind → typed config class, with the parse/validate/load family.

`parse_config` is the ONE place `get_config_class(...).model_validate(...)`
is called; the others compose from it.
"""

from feeds.configs import CuratedFeedConfig, FeedConfig
from feeds.models import Feed
from feeds.policy import enforce_policy

_REGISTRY: dict[str, type[FeedConfig]] = {
    CuratedFeedConfig.FEED_KIND: CuratedFeedConfig,
}


def get_config_class(kind: str) -> type[FeedConfig]:
    return _REGISTRY[kind]


def parse_config(kind: str, data: dict) -> FeedConfig:
    """kind + raw dict -> typed config, SHAPE ONLY (no policy).

    The ONE place get_config_class(...).model_validate(...) is called.
    Use this (not validate_config) when policy must run on a later-derived
    object (e.g. build_update merges submitted+prior then enforces).

    Raises PydanticValidationError on a shape violation.
    """
    return get_config_class(kind).model_validate(data)


def validate_config(kind: str, data: dict) -> FeedConfig:
    """Untrusted input -> policy-safe typed config: parse_config (shape)
    + enforce_policy fused so a callsite can't forget the policy half.
    For WRITES where the returned object is what persists (create).

    Raises PydanticValidationError (shape) or PolicyError (policy).
    """
    return enforce_policy(parse_config(kind, data))


def load_config(feed: Feed) -> FeedConfig:
    """At-rest feed -> typed config, shape only (see parse_config). No
    policy: stored data is already normalized."""
    return parse_config(str(feed.kind), feed.data or {})
