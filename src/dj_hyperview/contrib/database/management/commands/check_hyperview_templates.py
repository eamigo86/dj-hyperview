"""Report stored template identity problems without changing database state."""

from typing import Any

from django.core.management.base import BaseCommand, CommandError, CommandParser
from django.db import connections

from ..._identity import _canonical_name_or_none, template_name_identity


class Command(BaseCommand):
    """Inspect names and identities on one explicitly selected database."""

    help = "Check Hyperview template identities without changing data or cache."
    requires_system_checks: list[str] = []

    def add_arguments(self, parser: CommandParser) -> None:
        """Require an explicit configured database alias.

        Args:
            parser: Django's command-line argument parser.
        """
        parser.add_argument(
            "--database",
            required=True,
            choices=tuple(connections),
            help="Database alias to inspect; no data will be modified.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        """Stream identity metadata and report deterministic row diagnostics.

        Args:
            *args: Positional command arguments supplied by Django.
            **options: Parsed options including the explicit database alias.

        Raises:
            CommandError: If the alias is invalid or any integrity issue is found.
            DatabaseError: If the selected database cannot be read.
        """
        from ...models import HyperviewTemplate

        using = options.get("database")
        if using not in connections:
            raise CommandError("Choose a configured database with --database.")
        rows = (
            HyperviewTemplate._base_manager.using(using)
            .order_by("pk")
            .values_list("pk", "name", "name_identity")
        )
        first_primary_keys: dict[str, int] = {}
        checked = 0
        issues = 0
        max_name_length = HyperviewTemplate._meta.get_field("name").max_length
        for primary_key, name, stored_identity in rows.iterator(chunk_size=1000):
            checked += 1
            problems: list[str] = []
            if _canonical_name_or_none(name) is None or len(name) > max_name_length:
                problems.append("invalid_name")
            expected_identity = template_name_identity(name)
            if stored_identity != expected_identity:
                problems.append("identity_mismatch")
            if expected_identity in first_primary_keys:
                first = first_primary_keys[expected_identity]
                problems.append(f"duplicate_identity (first_pk={first})")
            else:
                first_primary_keys[expected_identity] = primary_key
            if problems:
                issues += len(problems)
                self.stdout.write(f"pk={primary_key}: {', '.join(problems)}")
        if issues:
            raise CommandError(
                f"Checked {checked} templates on database '{using}': "
                f"found {issues} integrity issues. No data was changed."
            )
        self.stdout.write(
            f"Checked {checked} templates on database '{using}': no integrity issues."
        )
