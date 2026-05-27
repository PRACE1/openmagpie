from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from engine import registry
from events.registry import hydrate
from events.services import EventKind, EventService
from listeners import registry as listeners_registry
from listeners.configs import SemanticListenerConfig
from listeners.services import ListenerService


class Command(BaseCommand):
    help = "Re-judge a listener's recent persisted hits (ad-hoc spot check)."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("listener_id", type=str, help="ULID of the listener")
        parser.add_argument("--engine", type=str, default="ollama")
        parser.add_argument("--limit", type=int, default=25)

    def handle(self, *args: Any, **options: Any) -> None:
        # System-level lookup so admin / debug doesn't need to know the account.
        listener = ListenerService.Global.get(options["listener_id"])
        event_svc = EventService(account_id=str(listener.account_id))
        config = listeners_registry.load_config(listener)
        threshold = config.hit_threshold if isinstance(config, SemanticListenerConfig) else 0.8

        engine = registry.get(options["engine"])
        events = event_svc.list_recent_for_listener(
            kind=EventKind.HIT, listener_id=str(listener.id), limit=options["limit"]
        )

        self.stdout.write(
            f"Re-judging {len(events)} persisted hit(s) against listener '{listener.name}' "
            f"using engine={engine.kind} model={getattr(engine, 'model', '?')} "
            f"hit_threshold={threshold:.2f}\n"
        )

        agreed = 0
        for event in events:
            observation = hydrate(event)
            result = engine.judge(observation, listener)
            is_hit = result.score >= threshold
            mark = "HIT " if is_hit else "miss"
            if is_hit:
                agreed += 1
            self.stdout.write(
                f"  [{mark}] score={result.score:.2f} ({result.latency_ms:>5}ms)  {observation.title[:70]}"
            )
            self.stdout.write(f"         {result.reason}")

        self.stdout.write(f"\n{agreed}/{len(events)} re-judged as hits (threshold={threshold:.2f})")
