from datetime import timedelta
from unittest import mock

import httpx
import ulid
from django.test import TestCase
from django.utils import timezone

from openmagpie_schema.watch_enums import WatchActionDelivery, WatchActionRunState
from watches import run_messages
from watches.actions.protocol import DeliveryContext, DeliveryItem, DeliveryResult
from watches.actions.webhook import WebhookAction
from watches.models import WatchAction


class WebhookDeliveryTests(TestCase):
    """WebhookAction HTTP-status classification + the unified payload shape (no
    DB needed: load_config is pure shape validation on the in-memory action)."""

    def _deliver_with_status(self, status: int) -> DeliveryResult:
        action = WatchAction(id=ulid.ulid(), kind="webhook", config={"url": "https://h.example.com/hook"})
        req = httpx.Request("POST", "https://h.example.com/hook")
        resp = httpx.Response(status, headers={"Location": "https://elsewhere.example/x"}, request=req)
        item = DeliveryItem(data={"source": "x", "external_id": "1"}, key="x:1", source_label="x", source_kind="x")
        context = DeliveryContext(watch_id="w", watch_name="n", delivery=WatchActionDelivery.INSTANT)
        # follow_redirects stays off, so a 3xx is a returned response, not a hop.
        with (
            mock.patch("watches.actions.webhook.destination_block_reason", return_value=None),
            mock.patch("watches.actions.webhook.httpx.request", return_value=resp),
        ):
            return WebhookAction().deliver(action, items=[item], context=context)

    def test_redirect_is_permanent_error_not_transient(self) -> None:
        # A 3xx never reaches the receiver and re-redirects on retry, so it's a
        # permanent misconfig -> ERRORED (not FAILED, not SUCCEEDED). The failed
        # attempt is still recorded: the DeliveryCall carries the 302.
        result = self._deliver_with_status(302)
        self.assertEqual(result.outcome.state, WatchActionRunState.ERRORED)
        self.assertEqual(result.outcome.error, run_messages.WEBHOOK_REDIRECT)
        self.assertEqual(result.outcome.result["http_status"], 302)
        assert result.call is not None
        self.assertEqual(result.call.http_status, 302)

    def test_transient_status_is_failed_with_call(self) -> None:
        # A 5xx is transient -> FAILED (retryable), and the attempt is logged
        # with its status on the DeliveryCall.
        result = self._deliver_with_status(503)
        self.assertEqual(result.outcome.state, WatchActionRunState.FAILED)
        assert result.call is not None
        self.assertEqual(result.call.http_status, 503)

    def test_success_carries_http_status(self) -> None:
        result = self._deliver_with_status(200)
        self.assertEqual(result.outcome.state, WatchActionRunState.SUCCEEDED)
        assert result.call is not None
        self.assertEqual(result.call.http_status, 200)
        self.assertEqual(result.outcome.result["http_status"], 200)

    def _capture_request(self, action: WatchAction, item: DeliveryItem, context: DeliveryContext) -> dict:
        """Deliver with a mocked transport, returning the captured request
        kwargs ({method, json, ...}) so a test can assert the wire shape."""
        captured: dict = {}
        req = httpx.Request("POST", "https://h.example.com/hook")
        resp = httpx.Response(200, request=req)

        def fake_request(method, url, **kwargs):
            captured.update(method=method, url=url, **kwargs)
            return resp

        with (
            mock.patch("watches.actions.webhook.destination_block_reason", return_value=None),
            mock.patch("watches.actions.webhook.httpx.request", side_effect=fake_request),
        ):
            WebhookAction().deliver(action, items=[item], context=context)
        return captured

    def test_payload_is_self_describing(self) -> None:
        # The unified body carries watch ref, cadence, the digest window, and
        # per-item key/score/source plus the include_fields-filtered item.
        action = WatchAction(
            id=ulid.ulid(),
            kind="webhook",
            config={"url": "https://h.example.com/hook", "include_fields": ["title"]},
        )
        item = DeliveryItem(
            data={"source": "reddit", "external_id": "abc", "title": "T", "url": "U"},
            key="reddit:abc",
            source_label="r/ClaudeAI",
            source_kind="reddit_subreddit",
            score=0.86,
        )
        since = timezone.now()
        context = DeliveryContext(
            watch_id="w1",
            watch_name="ai-webhook",
            delivery=WatchActionDelivery.DIGEST,
            window_since=since,
            window_until=since + timedelta(seconds=3600),
        )
        body = self._capture_request(action, item, context)["json"]
        self.assertEqual(body["watch"], {"id": "w1", "name": "ai-webhook"})
        self.assertEqual(body["delivery"], "digest")
        self.assertIsNotNone(body["window"])
        (sent,) = body["items"]
        self.assertEqual(sent["key"], "reddit:abc")
        self.assertEqual(sent["score"], 0.86)
        self.assertEqual(sent["source"], {"label": "r/ClaudeAI", "kind": "reddit_subreddit"})
        self.assertEqual(sent["item"], {"title": "T"})  # url dropped by include_fields

    def test_method_is_dispatched(self) -> None:
        # A configured PUT is the verb actually sent (not hard-coded POST).
        action = WatchAction(
            id=ulid.ulid(), kind="webhook", config={"url": "https://h.example.com/hook", "method": "PUT"}
        )
        item = DeliveryItem(data={"source": "x", "external_id": "1"}, key="x:1", source_label="x", source_kind="x")
        context = DeliveryContext(watch_id="w", watch_name="n", delivery=WatchActionDelivery.INSTANT)
        self.assertEqual(self._capture_request(action, item, context)["method"], "PUT")
