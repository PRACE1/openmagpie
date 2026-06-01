"""WatchActionService: account-scoped CRUD on the action chain of a path.

Owns everything about a path's `WatchAction` rows: the ordered chain
read, whole-chain replace (used by watch create/update), and the
per-action sub-router mutations (add at a rank, remove + renumber).

Rank is dense (0..N-1, contiguous) within a path, unique `(account_id,
path_id, rank)`. Concurrent chain mutations on one path are serialized by
`path_chain_lock(path_id)` (cache-backed, the same primitive as
`feed_set_lock`) ; the path is the right grain since rank uniqueness is
per-path, and locking the path (not its rows) also covers the
add-first-action race a row lock would miss. The loser of the race gets
`ConcurrentChainError` -> 409. At ~2-10 actions/path the renumber is free.
"""

import builtins

from django.db import transaction
from django.utils import timezone

from common.locks import path_chain_lock
from openmagpie_schema.watch import WatchActionInput
from openmagpie_schema.watch_actions import WatchActionConfigBase
from watches.models import WatchAction
from watches.registry import load_config, merge_config, validate_config


class ConcurrentChainError(RuntimeError):
    """Another chain mutation holds `path_chain_lock` for this path. The
    caller maps this to a 409 ; the operator retries."""


class WatchActionService:
    """Account-scoped service for a path's WatchAction chain."""

    def __init__(self, *, account_id: str) -> None:
        if not account_id:
            raise ValueError("WatchActionService requires account_id")
        self.account_id = account_id

    # ── Reads ──────────────────────────────────────────────────────────

    def list_for_path(self, path_id: str, /) -> builtins.list[WatchAction]:
        """The path's actions, dense rank order (the chain)."""
        return builtins.list(WatchAction.objects.filter(account_id=self.account_id, path_id=path_id).order_by("rank"))

    def get(self, action_id: str, /) -> WatchAction:
        """Raises WatchAction.DoesNotExist if missing / other-account."""
        return WatchAction.objects.get(id=action_id, account_id=self.account_id)

    # ── Writes ─────────────────────────────────────────────────────────

    def replace_chain(self, *, path_id: str, actions: builtins.list[WatchActionInput]) -> builtins.list[WatchAction]:
        """Replace a path's whole chain with `actions` (list order = rank
        0..N-1). Used by watch create/update ; an empty `actions` clears
        the chain (a watch with no actions yet). Each input's `config` is
        re-validated (shape + policy) so the persisted blob is normalized ;
        `kind` is the spec's top-level discriminator (the registry
        validates the blob against it).

        Takes `path_chain_lock` like `add`/`remove`: a blind delete-all +
        bulk-insert is self-consistent against another replace, but it
        still races the read-modify-write verbs (an `add` snapshotting +
        renumbering rows this is deleting). The lock serializes all three
        chain-mutators on the path. Raises `ConcurrentChainError` if a
        chain edit is already in progress.

        Self-locking (not the caller's job) because the lock is chain
        state, not watch state ; the WatchService create/update wraps only
        the watch-scalar + feed writes in its own transaction. A new path
        in create has no concurrent traffic, so the lock is uncontended
        there ; on edit it's the real serialization point.

        Edit-round-trip secrets are carried forward via `merge_preserving`:
        a whole-chain replace has no stable per-action identity, so the
        new i-th action OF A KIND pairs with the prior i-th action of that
        same kind (the index-within-kind heuristic feeds uses for its
        notifier list). The prior chain is read INSIDE the lock so the
        snapshot can't race a concurrent edit. Today's semantic_filter has
        no secrets (merge returns self) ; the wiring is for the
        secret-bearing kinds (webhook/log)."""
        with path_chain_lock(path_id) as acquired:
            if not acquired:
                raise ConcurrentChainError(f"another chain edit is in progress on path {path_id}; retry")
            priors_by_kind = self._priors_by_kind(path_id)
            seen_by_kind: dict[str, int] = {}
            rows: builtins.list[WatchAction] = []
            for rank, spec in enumerate(actions):
                i = seen_by_kind.get(spec.kind, 0)
                seen_by_kind[spec.kind] = i + 1
                same_kind = priors_by_kind.get(spec.kind, [])
                prior = same_kind[i] if i < len(same_kind) else None
                merged = merge_config(spec.kind, spec.config, prior)
                rows.append(
                    WatchAction(
                        account_id=self.account_id,
                        path_id=path_id,
                        kind=spec.kind,
                        config=merged.model_dump(mode="json"),
                        rank=rank,
                    )
                )
            with transaction.atomic():
                WatchAction.objects.filter(account_id=self.account_id, path_id=path_id).delete()
                if rows:
                    WatchAction.objects.bulk_create(rows)
        return self.list_for_path(path_id)

    def _priors_by_kind(self, path_id: str) -> dict[str, builtins.list[WatchActionConfigBase]]:
        """The path's current action configs grouped by kind, in rank order.
        Feeds the index-within-kind pairing in `replace_chain` (the i-th
        new action of a kind merges against the i-th prior of that kind).
        Call inside the chain lock so the snapshot is race-free."""
        out: dict[str, builtins.list[WatchActionConfigBase]] = {}
        for action in self.list_for_path(path_id):
            out.setdefault(str(action.kind), []).append(load_config(action))
        return out

    def add(self, *, path_id: str, action: WatchActionInput, rank: int | None = None) -> WatchAction:
        """Insert one action into the chain. `rank=None` appends ; an
        explicit rank inserts there, shifting later actions up. Renumbers
        to keep the chain dense. Returns the created row. Raises
        `ConcurrentChainError` if another chain mutation holds the path
        lock."""
        config = validate_config(action.kind, action.config)
        with path_chain_lock(path_id) as acquired:
            if not acquired:
                raise ConcurrentChainError(f"another chain edit is in progress on path {path_id}; retry")
            with transaction.atomic():
                chain = self.list_for_path(path_id)
                insert_at = len(chain) if rank is None else max(0, min(rank, len(chain)))
                # Save the new row at a temporary rank past the end so it
                # can't collide with an existing rank; _renumber then
                # assigns every row its final dense rank.
                created = WatchAction.objects.create(
                    account_id=self.account_id,
                    path_id=path_id,
                    kind=action.kind,
                    config=config.model_dump(mode="json"),
                    rank=len(chain) + 1,
                )
                chain.insert(insert_at, created)
                self._renumber(chain)
        return created

    def set_config(self, action: WatchAction, /, *, spec: WatchActionInput) -> WatchAction:
        """Replace one action's config in place (same rank, same row).
        `action` is the existing row ; `spec` is the new desired state.

        The new config is re-validated (shape + merge + policy) so the
        persisted blob is normalized ; `kind` is the spec's top-level
        discriminator and MAY change (a node can switch kind, e.g. swap one
        filter for another). When the kind is UNCHANGED, the prior config
        is fed to `merge_preserving` so edit-round-trip state (a redacted
        secret the operator left masked) is carried forward ; on a kind
        change there's no comparable prior, so the submitted config wins
        wholesale. No rank change and no chain renumber, so no
        `path_chain_lock` is needed: this touches exactly one row and the
        unique `(path_id, rank)` is untouched."""
        if str(action.account_id) != self.account_id:
            raise ValueError(f"action account_id mismatch: {action.account_id!r} not in scope {self.account_id!r}")
        prior = load_config(action) if str(action.kind) == spec.kind else None
        merged = merge_config(spec.kind, spec.config, prior)
        action.kind = spec.kind
        action.config = merged.model_dump(mode="json")
        action.save(update_fields=["kind", "config", "updated_at"])
        return action

    def remove(self, action: WatchAction, /) -> None:
        """Delete one action and close the rank gap on its path. Raises
        `ConcurrentChainError` if another chain mutation holds the lock."""
        if str(action.account_id) != self.account_id:
            raise ValueError(f"action account_id mismatch: {action.account_id!r} not in scope {self.account_id!r}")
        path_id = str(action.path_id)
        with path_chain_lock(path_id) as acquired:
            if not acquired:
                raise ConcurrentChainError(f"another chain edit is in progress on path {path_id}; retry")
            with transaction.atomic():
                action.delete()
                self._renumber(self.list_for_path(path_id))

    def _renumber(self, ordered: builtins.list[WatchAction]) -> None:
        """Assign dense ranks 0..N-1 in list order and persist. All rows
        must already be saved (bulk_update only UPDATEs).

        Two-phase to dodge the unique `(path_id, rank)` constraint: first
        offset every row past the max live rank, then write the final
        ranks. A single-pass update would transiently collide (row B
        taking row A's old rank before A has moved).

        `updated_at` is set by hand on the final pass: `bulk_update` does
        NOT fire the `auto_now` the model relies on, so without this the
        stale in-memory timestamp would be rewritten unchanged. The first
        (offset) pass writes `rank` only ; it's a transient internal state,
        not a real edit, so it deliberately doesn't touch `updated_at`."""
        now = timezone.now()
        offset = len(ordered) + 1
        for i, row in enumerate(ordered):
            row.rank = i + offset
        WatchAction.objects.bulk_update(ordered, ["rank"])
        for i, row in enumerate(ordered):
            row.rank = i
            row.updated_at = now
        WatchAction.objects.bulk_update(ordered, ["rank", "updated_at"])
