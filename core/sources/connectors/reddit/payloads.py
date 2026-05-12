"""Pydantic shapes for Reddit's wire format (the JSON returned by /r/<sub>/new.json).

These describe what Reddit sends us — the *transport* layer. Distinct from
`reddit_observations.py`, which describes our internal `Observation` shape
that the engine + persistence layer reads.

`extra="ignore"` everywhere so Reddit adding new fields doesn't break us;
only the subset we read is validated.
"""

from pydantic import BaseModel


class RedditPostPayload(BaseModel):
    """The `data` blob inside a `t3` (post) listing child. Subset of Reddit's
    per-post fields — only what we project into NewRedditPostObservation."""

    id: str
    created_utc: float  # unix seconds
    permalink: str
    title: str = ""
    selftext: str = ""
    # Reddit returns null for deleted users — coerce at the call site (`or ""`).
    author: str | None = None
    subreddit: str = ""
    score: int = 0
    num_comments: int = 0
    upvote_ratio: float = 1.0
    is_self: bool = True
    over_18: bool = False

    model_config = {"extra": "ignore"}


class RedditListingChild(BaseModel):
    """One entry in a Reddit listing. `kind` is the "thing prefix" — `t3` for
    posts, `t1` for comments, etc. /new/.json should only return t3; we don't
    validate the prefix here, trusting the endpoint."""

    kind: str
    data: RedditPostPayload

    model_config = {"extra": "ignore"}


class RedditListingData(BaseModel):
    """The `data` envelope of a listing. `after` is the pagination cursor;
    null at end of feed."""

    after: str | None = None
    before: str | None = None
    children: list[RedditListingChild]

    model_config = {"extra": "ignore"}


class RedditListing(BaseModel):
    """Top-level Reddit listing response: `{kind: "Listing", data: {...}}`."""

    kind: str
    data: RedditListingData

    model_config = {"extra": "ignore"}
