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
