"""Regression tests for the feeds app.

`SpecHashCanonicalTests` pins the sha256 produced by
`feeds.services.sources._hash_spec` for a known spec (one case per
spec kind plus an order-independence check). If a future change to
the SourceSpec field set or field-declaration order silently
changes the hash, these tests break; the fix is to add a data
migration that recomputes existing rows' `spec_hash` column, then
update the pinned values below to the new canonical hash. The `(account_id, feed_id,
spec_hash)` unique constraint is the dedup key on set_sources, so
a silent hash drift would break re-imports.
"""

from types import SimpleNamespace
from unittest import mock

import ulid
from django.test import SimpleTestCase, TestCase

from feeds.models import Feed
from feeds.services.polling import FeedPollOperation
from feeds.services.sources import _hash_spec
from openmagpie_schema.configs import RedditSubredditSourceSpec, RssSourceSpec


class PollHeartbeatTests(TestCase):
    """The poll loop renews its lease per source (so a large feed polls under
    one held lock) and stops early if the lease is lost."""

    def _op_over_n_sources(self, n: int, *, heartbeat) -> FeedPollOperation:
        feed = Feed.objects.create(account_id=ulid.ulid(), user_id=ulid.ulid(), name="f", kind="curated", data={})
        op = FeedPollOperation(feed, heartbeat=heartbeat)
        sources = [
            SimpleNamespace(id=f"s{i}", spec={"kind": "rss", "url": f"https://x{i}.test/rss", "name": f"s{i}"})
            for i in range(n)
        ]
        # Inject fake sources before the cached_property fires (no DB rows).
        op.__dict__["source_svc"] = SimpleNamespace(list=lambda _feed: sources)
        return op

    def test_heartbeat_called_once_per_source(self) -> None:
        calls = {"n": 0}

        def hb() -> bool:
            calls["n"] += 1
            return True

        op = self._op_over_n_sources(3, heartbeat=hb)
        with mock.patch.object(op, "_poll_source", return_value=(0, 0)):  # no HTTP
            op.run()
        self.assertEqual(calls["n"], 3)

    def test_lost_lease_stops_the_cycle_early(self) -> None:
        # Lease reported lost after the first source -> the loop must stop.
        calls = {"n": 0}

        def hb() -> bool:
            calls["n"] += 1
            return calls["n"] <= 1

        op = self._op_over_n_sources(5, heartbeat=hb)
        with mock.patch.object(op, "_poll_source", return_value=(0, 0)) as poll:
            op.run()
        self.assertEqual(poll.call_count, 1)  # stopped, didn't poll the other 4


class SpecHashCanonicalTests(SimpleTestCase):
    def test_reddit_subreddit_pinned_hash(self) -> None:
        spec = RedditSubredditSourceSpec(subreddit="ClaudeAI")
        self.assertEqual(
            _hash_spec(spec),
            "0da1f0763888956b29fd3ed95ef61a7b847f94c982ee458fedc7562a6c171a80",
        )

    def test_rss_pinned_hash(self) -> None:
        spec = RssSourceSpec(url="https://example.com/feed.rss", name="Example")
        self.assertEqual(
            _hash_spec(spec),
            "fd85eb925e162f0a2644eff338e98f1a4693d2afe74d7597852c99c912c8f903",
        )

    def test_hash_is_order_independent_for_dict_inputs(self) -> None:
        """Two specs with the same fields produce the same hash
        regardless of which order they were constructed in. Guards
        against a future field reorder on the SourceSpec subclass
        producing a different `model_dump` ordering."""
        a = RssSourceSpec(url="https://example.com/feed.rss", name="A")
        b = RssSourceSpec(name="A", url="https://example.com/feed.rss")
        self.assertEqual(_hash_spec(a), _hash_spec(b))
