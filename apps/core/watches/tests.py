from unittest import mock

import httpx
import ulid
from django.test import TestCase
from django.utils import timezone

from openmagpie_schema.watch import WatchActionInput
from openmagpie_schema.watch_actions import WebhookConfig
from openmagpie_schema.watch_enums import WatchActionRunState
from watches import run_messages
from watches.actions.protocol import ActionOutcome
from watches.actions.webhook import WebhookAction
from watches.models import WatchAction, WatchActionRun
from watches.policy import PolicyError
from watches.registry import load_config
from watches.services import WatchActionRunService, WatchService


class ReplaceChainUpsertTests(TestCase):
    """replace_chain upserts by action id: known id updates in place, no id
    is new, absent rows are deleted, ranks renumber densely."""

    def setUp(self) -> None:
        self.account_id = ulid.ulid()
        self.wsvc = WatchService(account_id=self.account_id)
        self.asvc = self.wsvc.action_svc

    def _logs(self, prefixes):
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[WatchActionInput(kind="log", config={"prefix": p}) for p in prefixes],
        )
        return watch, self.asvc.list_for_path(watch.initial_path_id)

    def test_remove_and_add_in_one_edit(self) -> None:
        # Regression: delete + add in one edit must not hit the unique
        # (path, rank) constraint during the dense renumber.
        watch, chain = self._logs(["[A]", "[B]", "[C]"])
        by = {r.config["prefix"]: r for r in chain}
        rows = self.asvc.replace_chain(
            path_id=watch.initial_path_id,
            actions=[
                WatchActionInput(id=str(by["[C]"].id), kind="log", config={"prefix": "[C]"}),
                WatchActionInput(id=str(by["[A]"].id), kind="log", config={"prefix": "[A]"}),
                WatchActionInput(kind="log", config={"prefix": "[D]"}),
            ],
        )
        self.assertEqual([(r.config["prefix"], r.rank) for r in rows], [("[C]", 0), ("[A]", 1), ("[D]", 2)])
        self.assertEqual(str(rows[0].id), str(by["[C]"].id))
        self.assertFalse(WatchAction.objects.filter(id=by["[B]"].id).exists())

    def test_reorder_preserves_ids(self) -> None:
        watch, (a, b) = self._logs(["[A]", "[B]"])
        rows = self.asvc.replace_chain(
            path_id=watch.initial_path_id,
            actions=[
                WatchActionInput(id=str(b.id), kind="log", config={"prefix": "[B]"}),
                WatchActionInput(id=str(a.id), kind="log", config={"prefix": "[A]"}),
            ],
        )
        self.assertEqual([str(r.id) for r in rows], [str(b.id), str(a.id)])
        self.assertEqual([r.rank for r in rows], [0, 1])

    def test_edit_preserves_action_id_and_run_history(self) -> None:
        watch, (a, b) = self._logs(["[A]", "[B]"])
        run = WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=str(watch.id),
            action_id=str(a.id),
            feed_item_id=ulid.ulid(),
            state="succeeded",
            scheduled_at=timezone.now(),
        )
        self.asvc.replace_chain(
            path_id=watch.initial_path_id,
            actions=[
                WatchActionInput(id=str(a.id), kind="log", config={"prefix": "[A2]"}),
                WatchActionInput(id=str(b.id), kind="log", config={"prefix": "[B]"}),
            ],
        )
        self.assertTrue(WatchActionRun.objects.filter(id=run.id, action_id=str(a.id)).exists())

    def test_unknown_id_rejected(self) -> None:
        watch, _ = self._logs(["[A]"])
        with self.assertRaises(PolicyError):
            self.asvc.replace_chain(
                path_id=watch.initial_path_id,
                actions=[WatchActionInput(id=ulid.ulid(), kind="log", config={"prefix": "[X]"})],
            )

    def test_reorder_two_webhooks_keeps_each_secret_with_its_endpoint(self) -> None:
        # The fixed 3b case: masked reorder restores each token to its own row.
        watch = self.wsvc.create(
            user_id=ulid.ulid(),
            name="t",
            feed_ids=[],
            actions=[
                WatchActionInput(
                    kind="webhook", config={"url": "https://a.example.com/h", "headers": {"Authorization": "tokA"}}
                ),
                WatchActionInput(
                    kind="webhook", config={"url": "https://b.example.com/h", "headers": {"Authorization": "tokB"}}
                ),
            ],
        )
        a, b = self.asvc.list_for_path(watch.initial_path_id)

        def masked(action):
            return WatchActionInput(id=str(action.id), kind="webhook", config=load_config(action).redacted_dump())

        rows = self.asvc.replace_chain(path_id=watch.initial_path_id, actions=[masked(b), masked(a)])
        cfg = {str(r.id): load_config(r) for r in rows}
        ca, cb = cfg[str(a.id)], cfg[str(b.id)]
        assert isinstance(ca, WebhookConfig) and isinstance(cb, WebhookConfig)
        self.assertEqual(ca.headers["Authorization"], "tokA")
        self.assertEqual(cb.headers["Authorization"], "tokB")

    def test_new_webhook_with_masked_secret_rejected(self) -> None:
        watch, _ = self._logs([])
        with self.assertRaises(PolicyError):
            self.asvc.replace_chain(
                path_id=watch.initial_path_id,
                actions=[
                    WatchActionInput(
                        kind="webhook", config={"url": "https://h.example.com/x", "headers": {"Authorization": "***"}}
                    ),
                ],
            )


class ActionRunListTests(TestCase):
    """list_for_action: newest-first, scoped to the action, state filter."""

    def setUp(self) -> None:
        self.account_id = ulid.ulid()
        self.runs = WatchActionRunService(account_id=self.account_id)

    def _run(self, action_id: str, state: str) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=ulid.ulid(),
            action_id=action_id,
            feed_item_id=ulid.ulid(),
            state=state,
            scheduled_at=timezone.now(),
        )

    def test_newest_first_state_filter_and_scoping(self) -> None:
        aid = ulid.ulid()
        made = [self._run(aid, s) for s in ("succeeded", "gated", "succeeded")]
        # newest-first = descending id (ULIDs in one ms aren't creation-ordered).
        expected = sorted((str(r.id) for r in made), reverse=True)
        self.assertEqual([str(r.id) for r in self.runs.list_for_action(aid)], expected)
        succeeded = {str(made[0].id), str(made[2].id)}
        self.assertEqual({str(r.id) for r in self.runs.list_for_action(aid, state="succeeded")}, succeeded)
        self.assertEqual(self.runs.list_for_action(ulid.ulid()), [])


class WebhookDeliveryTests(TestCase):
    """WebhookAction._deliver HTTP-status classification (no DB needed:
    load_config is pure shape validation on the in-memory action)."""

    def _run_with_status(self, status: int) -> ActionOutcome:
        action = WatchAction(id=ulid.ulid(), kind="webhook", config={"url": "https://h.example.com/hook"})
        req = httpx.Request("POST", "https://h.example.com/hook")
        resp = httpx.Response(status, headers={"Location": "https://elsewhere.example/x"}, request=req)
        # follow_redirects stays off, so a 3xx is a returned response, not a hop.
        with (
            mock.patch("watches.actions.webhook.destination_block_reason", return_value=None),
            mock.patch("watches.actions.webhook.httpx.post", return_value=resp),
        ):
            return WebhookAction().run(action, item_data={"source": "x", "external_id": "1"})

    def test_redirect_is_permanent_error_not_transient(self) -> None:
        # A 3xx never reaches the receiver and re-redirects on retry, so it's a
        # permanent misconfig -> ERRORED (not a retryable raise, not SUCCEEDED).
        outcome = self._run_with_status(302)
        self.assertEqual(outcome.state, WatchActionRunState.ERRORED)
        self.assertEqual(outcome.error, run_messages.WEBHOOK_REDIRECT)
        self.assertEqual(outcome.result["http_status"], 302)
