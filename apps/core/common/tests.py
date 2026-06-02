from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from common.commands import SingleFlightCommand


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
