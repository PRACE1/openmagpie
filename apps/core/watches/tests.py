import time
from datetime import timedelta
from unittest import mock

import httpx
import ulid
from django.conf import settings
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from auth_api.operations.signup import SignupOperation
from openmagpie_schema.watch import WatchActionInput
from openmagpie_schema.watch_actions import WebhookConfig
from openmagpie_schema.watch_enums import WatchActionRunState
from watches import run_messages
from watches.actions.protocol import ActionOutcome
from watches.actions.webhook import WebhookAction
from watches.management.commands.process_due_runs import _fmt_duration, _progress
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


class CountDueTests(TestCase):
    """count_due (sizes the drain's progress/ETA line) must report exactly
    what claim_due would yield — same `_due_runs` filter, no claim."""

    def setUp(self) -> None:
        self.now = timezone.now()

    def _run(self, *, state: str, scheduled_at, attempts: int = 0) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=ulid.ulid(),
            watch_id=ulid.ulid(),
            action_id=ulid.ulid(),
            feed_item_id=ulid.ulid(),
            state=state,
            attempts=attempts,
            scheduled_at=scheduled_at,
        )

    def test_count_matches_claim_due_and_honors_the_due_filter(self) -> None:
        past, future = self.now - timedelta(minutes=1), self.now + timedelta(minutes=1)
        # Due: pending + retryable-failed, scheduled in the past, under the cap.
        self._run(state="pending", scheduled_at=past)
        self._run(state="failed", scheduled_at=past)
        # Not due: future schedule, terminal states, attempts at the cap.
        self._run(state="pending", scheduled_at=future)
        self._run(state="succeeded", scheduled_at=past)
        self._run(state="gated", scheduled_at=past)
        self._run(state="pending", scheduled_at=past, attempts=settings.WATCH_RUN_MAX_ATTEMPTS)

        self.assertEqual(WatchActionRunService.Global.count_due(now=self.now), 2)
        # claim_due drains (mutates) the same set, so count it last.
        claimed = list(WatchActionRunService.Global.claim_due(now=self.now))
        self.assertEqual(len(claimed), 2)


class ProgressFormatTests(TestCase):
    """The drain's ETA string: coarse h/m/s, remaining floored at 0."""

    def test_fmt_duration_buckets(self) -> None:
        self.assertEqual(_fmt_duration(9), "9s")
        self.assertEqual(_fmt_duration(184), "3m04s")
        self.assertEqual(_fmt_duration(3700), "1h01m")

    def test_progress_eta_and_floor(self) -> None:
        # 2 of 10 done in 20s -> 10s/run avg, 8 left -> ~80s = 1m20s.
        self.assertEqual(_progress(2, 10, time.monotonic() - 20), "[2/10, ~1m20s left]")
        # More fell due than the snapshot: remaining floors at 0, never negative.
        self.assertEqual(_progress(12, 10, time.monotonic() - 20), "[12/10, ~0s left]")


class LeafActionRouteTests(TestCase):
    """Per-action endpoints addressed by the action's own id at
    `/v1/actions/<action_id>` (no watch id): set / remove / runs, plus
    account isolation."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="leaf@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _make_watch_with_action(self) -> tuple[str, str]:
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.assertEqual(resp.status_code, 201, resp.content)
        body = resp.json()
        return body["id"], body["actions"][0]["id"]

    def test_runs_set_remove_by_action_id_only(self) -> None:
        _watch_id, action_id = self._make_watch_with_action()

        # runs: 200 with an empty log (no runs yet), no watch id in the path.
        runs = self.client.get(f"/v1/actions/{action_id}/runs")
        self.assertEqual(runs.status_code, 200, runs.content)
        self.assertEqual(runs.json()["items"], [])

        # set: replace the config in place.
        put = self.client.put(f"/v1/actions/{action_id}", {"kind": "log", "config": {"prefix": "[B]"}}, format="json")
        self.assertEqual(put.status_code, 200, put.content)
        self.assertEqual(put.json()["id"], action_id)

        # remove: 204, and it's gone.
        self.assertEqual(self.client.delete(f"/v1/actions/{action_id}").status_code, 204)
        self.assertFalse(WatchAction.objects.filter(id=action_id).exists())

    def test_unknown_action_id_is_404(self) -> None:
        self.assertEqual(self.client.get(f"/v1/actions/{ulid.ulid()}/runs").status_code, 404)

    def test_another_account_cannot_reach_the_action(self) -> None:
        _watch_id, action_id = self._make_watch_with_action()
        other = APIClient()
        other.force_authenticate(user=SignupOperation(email="other@example.com", password="Str0ng-Passw0rd!").run())
        # Same opaque 404 whether the action is absent or owned by someone else.
        self.assertEqual(other.get(f"/v1/actions/{action_id}/runs").status_code, 404)
        self.assertEqual(
            other.put(
                f"/v1/actions/{action_id}", {"kind": "log", "config": {"prefix": "x"}}, format="json"
            ).status_code,
            404,
        )
        self.assertEqual(other.delete(f"/v1/actions/{action_id}").status_code, 404)
        # ... and the owner's action is untouched.
        self.assertTrue(WatchAction.objects.filter(id=action_id).exists())


class ActionActivitySummaryTests(TestCase):
    """The activity summary windows the evaluated breakdown by COMPLETION
    (evaluation) time and reports the live pending/running backlog."""

    def setUp(self) -> None:
        self.user = SignupOperation(email="summary@example.com", password="Str0ng-Passw0rd!").run()
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        resp = self.client.post(
            "/v1/watches",
            {"name": "w", "feed_ids": [], "actions": [{"kind": "log", "config": {"prefix": "[A]"}}]},
            format="json",
        )
        self.action_id = resp.json()["actions"][0]["id"]
        self.account_id = WatchAction.objects.get(id=self.action_id).account_id

    def _run(self, state: str, *, completed_at=None) -> WatchActionRun:
        return WatchActionRun.objects.create(
            account_id=self.account_id,
            watch_id=ulid.ulid(),
            action_id=self.action_id,
            feed_item_id=ulid.ulid(),
            state=state,
            scheduled_at=timezone.now(),
            completed_at=completed_at,
        )

    def test_evaluated_is_windowed_by_completion_backlog_is_live(self) -> None:
        now = timezone.now()
        self._run("succeeded", completed_at=now - timedelta(hours=1))  # in the 7d window
        self._run("gated", completed_at=now - timedelta(hours=2))  # in window
        self._run("succeeded", completed_at=now - timedelta(days=10))  # outside the window
        self._run("pending")  # backlog (no completion time)
        self._run("pending")
        self._run("running")
        resp = self.client.get(f"/v1/actions/{self.action_id}/runs", {"window": "7d"})
        self.assertEqual(resp.status_code, 200, resp.content)
        s = resp.json()["summary"]
        self.assertEqual(s["window"], "7d")  # echoes the requested preset
        # The 10-day-old succeeded run is excluded by the window.
        self.assertEqual(s["evaluated"], {"succeeded": 1, "gated": 1})
        self.assertEqual(s["pending"], 2)
        self.assertEqual(s["running"], 1)

    def test_summary_omitted_while_paging(self) -> None:
        resp = self.client.get(f"/v1/actions/{self.action_id}/runs", {"after": ulid.ulid()})
        self.assertIsNone(resp.json()["summary"])

    def test_bad_window_is_400(self) -> None:
        self.assertEqual(self.client.get(f"/v1/actions/{self.action_id}/runs", {"window": "bogus"}).status_code, 400)
