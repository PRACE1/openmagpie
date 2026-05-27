from datetime import UTC, datetime
from typing import TYPE_CHECKING, ClassVar

from events.observations import Observation

if TYPE_CHECKING:
    from openmagpie_schema.configs import RedditSubredditStreamSpec

    from .payloads import RedditPostPayload


class NewRedditPostObservation(Observation):
    """A new top-level post observed in a watched subreddit."""

    EVENT_KIND: ClassVar[str] = "new_post"

    # Reddit-specific fields. `author` lives here (not on Observation base) because
    # "who emitted this" is a source-shaped concept, Slack has `user`, scheduled
    # jobs have nothing, etc.
    author: str = ""
    permalink: str
    subreddit: str = ""
    score: int = 0
    num_comments: int = 0
    upvote_ratio: float = 1.0
    is_self: bool = True
    over_18: bool = False

    model_config = {"frozen": True, "extra": "ignore"}

    def stream_slug(self) -> str:
        return self.subreddit

    @classmethod
    def sample(cls, variant: int = 0) -> "NewRedditPostObservation":
        # 1-indexed for the operator-visible id/url/title (so `variant=0`
        # reads as "post 1", `variant=1` as "post 2"). Keeps every field
        # that the receiver might key on (external_id, url, permalink,
        # title) honestly distinct across variants.
        n = variant + 1
        slug = f"1example{n}"
        return cls(
            external_id=slug,
            kind=cls.EVENT_KIND,
            occurred_at=datetime(2026, 5, 27, 12, 0, tzinfo=UTC),
            source="reddit_subreddit",
            title=f"Example post {n}: matched this listener",
            content="(Observation.content — included in payload only if `include_fields` lists it.)",
            url=f"https://www.reddit.com/r/example/comments/{slug}/example_post_{n}/",
            parent_external_id="",
            subreddit="example",
            permalink=f"/r/example/comments/{slug}/example_post_{n}/",
            author="example_user",
            score=42,  # Reddit upvotes; distinct from engine relevance_score
            num_comments=7,
            upvote_ratio=0.95,
            is_self=True,
            over_18=False,
        )

    @classmethod
    def from_reddit_blob(
        cls,
        raw: "RedditPostPayload",
        spec: "RedditSubredditStreamSpec",
    ) -> "NewRedditPostObservation":
        return cls(
            external_id=raw.id,
            kind=cls.EVENT_KIND,
            occurred_at=datetime.fromtimestamp(raw.created_utc, tz=UTC),
            source=spec.kind,
            title=raw.title,
            content=raw.selftext,
            author=raw.author or "",  # Reddit returns null for deleted users
            url=f"https://www.reddit.com{raw.permalink}",
            permalink=raw.permalink,
            subreddit=raw.subreddit,
            score=raw.score,
            num_comments=raw.num_comments,
            upvote_ratio=raw.upvote_ratio,
            is_self=raw.is_self,
            over_18=raw.over_18,
        )
