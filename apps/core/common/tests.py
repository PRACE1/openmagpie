from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from common.commands import SingleFlightCommand
from common.locks import named_lock

_LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "lock-tests"}}


@override_settings(CACHES=_LOCMEM)
class NamedLockLeaseTests(SimpleTestCase):
    """The cache-backed try-lock + its renewable lease."""

    def setUp(self) -> None:
        cache.clear()

    def test_second_holder_is_blocked_then_released(self) -> None:
        with named_lock(name="k", timeout=60) as a:
            self.assertTrue(a)
            with named_lock(name="k", timeout=60) as b:
                self.assertFalse(b)  # held -> miss
        # Released on exit: a fresh acquire succeeds.
        with named_lock(name="k", timeout=60) as c:
            self.assertTrue(c)

    def test_renew_extends_while_held(self) -> None:
        with named_lock(name="k", timeout=60) as lease:
            self.assertTrue(lease.renew())  # still ours
            self.assertTrue(lease.acquired)

    def test_renew_false_when_taken_over_and_release_spares_new_owner(self) -> None:
        with named_lock(name="k", timeout=60) as lease:
            # Simulate the lease expiring under us + another holder acquiring.
            cache.set("k", "someone-else", timeout=60)
            self.assertFalse(lease.renew())  # we no longer own it
            self.assertFalse(lease)  # __bool__ flips to False
        # Our finally must NOT delete the new owner's key.
        self.assertEqual(cache.get("k"), "someone-else")

    def test_missed_lock_never_renews(self) -> None:
        with named_lock(name="k", timeout=60), named_lock(name="k", timeout=60) as miss:
            self.assertFalse(miss)
            self.assertFalse(miss.renew())


class ResolveJobNameTests(SimpleTestCase):
    """`SingleFlightCommand.resolve_job_name`, the single-flight lock key."""

    def test_defaults_to_app_qualified_command_name(self) -> None:
        """For a real management-command module the key is the
        app-qualified `<app>.<command>` (so same-named commands in two apps
        don't share a lock)."""

        class Command(SingleFlightCommand):
            pass

        Command.__module__ = "watches.management.commands.process_due_runs"
        self.assertEqual(Command().resolve_job_name(), "watches.process_due_runs")

    def test_class_variable_overrides_default(self) -> None:
        """An explicit `job_name` wins over the derived file name (and
        bypasses the module-path requirement, by design)."""

        class Command(SingleFlightCommand):
            job_name = "shared_pipeline"

        Command.__module__ = "watches.services.something"
        self.assertEqual(Command().resolve_job_name(), "shared_pipeline")

    def test_non_command_module_without_override_raises(self) -> None:
        """Deriving the default outside a management-command module would
        lock on a misleading key, so it fails loud instead."""

        class Command(SingleFlightCommand):
            pass

        Command.__module__ = "watches.services.something"
        with self.assertRaises(ImproperlyConfigured):
            Command().resolve_job_name()
