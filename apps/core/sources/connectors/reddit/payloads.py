"""Projection of one Atom `<entry>` from Reddit's `.rss` endpoint.

Reddit's anonymous `.json` is gated by a TLS-fingerprint / HTTP-2 check that
Python clients can't pass (cookies, UA, Referer, and `Sec-Fetch-*` headers are
all insufficient, only a real browser handshake gets through). The `.rss`
endpoint serves the same /new listing as Atom XML, with no fingerprint check,
so we use it as the anon transport.

What we lose vs `.json`: `score`, `num_comments`, `upvote_ratio`, `is_self`,
`over_18`. Those fields are not present in the Atom feed. The long-term fix
for richer payloads is authenticated PRAW against oauth.reddit.com.
"""

from pydantic import BaseModel


class RedditAtomEntry(BaseModel):
    """One `<entry>` from the Reddit `.rss` Atom feed, projected to the fields
    we actually read. `atom_id` is Reddit's thing-id (e.g. `t3_1tqbykk`);
    `link` is the absolute comments URL; `content_html` is the post body as
    HTML inside an `<!-- SC_OFF --> ... <!-- SC_ON -->` envelope (or media
    boilerplate for link posts)."""

    atom_id: str
    title: str = ""
    published: str  # ISO 8601, e.g. "2026-05-28T18:26:14+00:00"
    content_html: str = ""
    link: str
    # Author is `/u/<name>` in the Atom feed; the connector strips the prefix.
    author_name: str = ""
    subreddit: str = ""

    model_config = {"extra": "ignore"}
