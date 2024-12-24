import json
import math
import random
import resource
import sys
import time
import uuid
from collections import defaultdict
from datetime import timedelta
from subprocess import CalledProcessError, check_call
import argparse
import pytz
from django_redis import get_redis_connection

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import BaseCommand, CommandError
from django.db import connection
from django.utils import timezone

from temba.archives.models import Archive
from temba.campaigns.models import Campaign, CampaignEvent
from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactField, ContactGroup, ContactGroupCount, ContactURN
from temba.flows.models import Flow
from temba.locations.models import AdminBoundary
from temba.msgs.models import Label
from temba.orgs.models import Org
from temba.utils import chunk_list
from temba.utils.dates import datetime_to_timestamp, timestamp_to_datetime

UPDATED__ = """
    # Sandbox Configuration Tool for RapidPro

    ## Description

    The `sandbox_setup` command is a dev/sandbox fixture management tool designed to extend RapidPro’s capabilities. By combining the functionality of test databases with enhanced configurability and version control, the tool will:

    1. Enable resetting organizations, users, and flows to initial predefined states.
    2. Standardize UUIDs across environments to ensure consistency.
    3. Allow data migration for sandbox flows, organizations, and configurations, keeping them current through version tracking and a lightweight Erlang-style `code_up` methodology for upgrades.
    4. Offer configurable and optional components, allowing granular control over which organizations, flows, and related entities are included in the sandbox environment.
    5. Apply database changes without requiring a full flush, enabling faster iteration cycles.

    By addressing these key points, the tool provides a consistent and efficient environment for running end-to-end tests, integrating components like Courier and Mailroom, and collaborating on configurations.

    * * *

    ## Personas

    ### DevOps Engineer - Jamie

    ```yaml
    - name: Jamie
      profile: Jamie is a mid-career DevOps engineer with experience managing CI/CD pipelines.
      dob: 1987-08-22
      income: $95,000
      location: Austin, TX
      bio: Jamie seeks tools to streamline sandbox configuration and eliminate repetitive tasks.
      impact: Jamie values automation and consistency in deploying environments for testing.
    ```

    ### Developer - Alex

    ```yaml
    - name: Alex
      profile: A backend developer working on RapidPro extensions.
      dob: 1991-04-15
      income: $80,000
      location: Berlin, Germany
      bio: Alex often struggles with managing multiple environment setups for feature testing.
      impact: Alex needs tools to quickly reset and test sandbox environments without conflicts.
    ```

    ### QA Analyst - Priya

    ```yaml
    - name: Priya
      profile: A QA analyst focusing on flow testing and reliability in multi-org environments.
      dob: 1994-06-10
      income: $70,000
      location: Bangalore, India
      bio: Priya aims to test updates in a controlled and predictable sandbox setting.
      impact: Priya requires reliable tools for setting up test scenarios and tracking configurations.
    ```

    * * *

    ## User Stories

    ### SND-001 - Restoring Sandbox to Initial State

    ```story
    - ticket-number: SND-001
      title: Restore sandbox environment
      profiles: [Jamie, Alex, Priya]
      story: |
          As a user managing a sandbox environment,
          I would like to reset my organization and flows to initial values,
          so that I can ensure a clean slate for testing.
      acceptance-criteria:
          - name: Reset Org
            criteria:
                Given a sandboxed organization,
                When I run `sandbox_config restore`,
                Then the organization and flows are reset to their predefined state.
    ```

    ### SND-002 - Data Migration Support

    ```story
    - ticket-number: SND-002
      title: Migrate sandbox configurations
      profiles: [Jamie, Alex]
      story: |
          As a developer,
          I would like to update sandbox data to the latest configurations,
          so that I can ensure compatibility with current implementations.
      acceptance-criteria:
          - name: Migrate Data
            criteria:
                Given outdated sandbox data,
                When I run `sandbox_config migrate`,
                Then the sandbox is updated to the latest flow and org definitions.
    ```

    ### SND-003 - Provide Feature-Specific Flows for Testing

    ```story
    - ticket-number: SND-003
      title: Access predefined flows for specific features
      profiles: [Alex, Priya]
      story: |
          As a developer or QA analyst,
          I would like access to predefined flows covering specific features like opt-outs and attachments,
          so that I can quickly test and validate these functionalities.
      acceptance-criteria:
          - name: List Feature Flows
            criteria:
                Given a sandbox environment,
                When I run `sandbox_config list --flows`,
                Then I see a list of predefined flows and their respective feature coverage.
          - name: Import Feature Flow
            criteria:
                Given a sandbox environment,
                When I run `sandbox_config load --flow=opt-out`,
                Then the opt-out feature flow is imported into the sandbox for testing.
    ```

    ### SND-004 - Create Known Orgs and Flows for Integration Testing

    ```story
    - ticket-number: SND-004
      title: Setup known orgs and flows
      profiles: [Jamie, Alex, Priya]
      story: |
          As a developer,
          I would like to set up predefined organizations and flows,
          so that I can ensure consistent integration tests across all components.
      acceptance-criteria:
          - name: Configure Known Org
            criteria:
                Given an integration test environment,
                When I run `sandbox_config setup --org=test-org`,
                Then the predefined organization and associated flows are created with fixed UUIDs.
    ```

    ### SND-005 - Facilitate Collaboration on Data Setup and Flow Structures

    ```story
    - ticket-number: SND-005
      title: Share data setup and flow structures
      profiles: [Alex, Priya]
      story: |
          As a developer or implementor,
          I would like to share sandbox configurations and flow structures,
          so that others can review, modify, and test them collaboratively.
      acceptance-criteria:
          - name: Export Configuration
            criteria:
                Given a sandbox environment,
                When I run `sandbox_config export --org=test-org`,
                Then the organization's setup and flows are exported in a shareable format.
          - name: Import Shared Configuration
            criteria:
                Given a sandbox environment,
                When I run `sandbox_config import --file=shared_config.json`,
                Then the sandbox is updated with the shared configuration.
    ```

    ### SND-006 - Simplify Sandbox Setup for Developers

    ```story
    - ticket-number: SND-006
      title: Straightforward sandbox setup
      profiles: [Jamie, Alex]
      story: |
          As a developer,
          I would like an easy-to-use setup command,
          so that I can quickly prepare a fully functional sandbox environment for testing.
      acceptance-criteria:
          - name: Quick Setup Command
            criteria:
                Given a clean environment,
                When I run `sandbox_config setup --all`,
                Then a complete sandbox is created, including organizations, flows, and other required components.
    ```

    ### SND-007 - Ensure Cross-Component Testing Compatibility

    ```story
    - ticket-number: SND-007
      title: Cross-component E2E test compatibility
      profiles: [Jamie, Alex, Priya]
      story: |
          As a developer,
          I would like to ensure that sandbox configurations are compatible with other components like Courier and Mailroom,
          so that I can perform end-to-end tests across the entire system.
      acceptance-criteria:
          - name: Validate Component Compatibility
            criteria:
                Given a sandbox environment,
                When I run `sandbox_config validate --components=courier,mailroom`,
                Then the tool checks and reports any configuration issues related to these components.
    ```

    ### SND-008 - Version Control for Sandbox Data

    ```story
    - ticket-number: SND-008
      title: Track and update sandbox data versions
      profiles: [Alex, Jamie]
      story: |
          As a developer,
          I would like to track versions of sandbox data and upgrade as needed,
          so that my environment remains up to date with the latest schema and flow definitions.
      acceptance-criteria:
          - name: List Sandbox Versions
            criteria:
                Given a sandbox environment,
                When I run `sandbox_config version --list`,
                Then I see a list of all current and available data versions.
          - name: Upgrade Sandbox Data
            criteria:
                Given a sandbox environment,
                When I run `sandbox_config upgrade`,
                Then all components are upgraded to their latest versions.
    ```

    ### SND-009 - Partial Sandbox Updates

    ```story
    - ticket-number: SND-009
      title: Support partial updates to sandbox
      profiles: [Alex, Priya]
      story: |
          As a developer,
          I would like to selectively update parts of my sandbox,
          so that I can test new configurations without disrupting existing setups.
      acceptance-criteria:
          - name: Update Single Flow
            criteria:
                Given a sandbox environment,
                When I run `sandbox_config update --flow=opt-out`,
                Then only the opt-out flow is updated to its latest version.
          - name: Update Org Settings
            criteria:
                Given a sandbox environment,
                When I run `sandbox_config update --org=test-org`,
                Then only the specified organization's settings are updated.
    ```
    """

import argparse
from django.core.management import BaseCommand


class SandboxArgParser:
    """
    A helper class that organizes argument definitions for the sandbox_setup command.
    We inline the logic for overriding 'default' and 'help' without a separate helper.
    """

    @staticmethod
    def common(parser: argparse.ArgumentParser, opts: dict = {}) -> None:
        """
        Adds common arguments used across multiple subcommands.
        Only override 'default' and 'help' if provided by opts.
        """
        g = parser.add_argument_group("Log/Debug Arguments", "Log Verbosity and Debug Settings.")

        # --verbose
        arg_key = "--verbose"
        if opts.get(arg_key) is not False:
            # The user may provide a dict with keys 'default' and/or 'help'
            arg_opts = opts.get(arg_key, {})
            g.add_argument(
                "-v",
                "--verbose",
                action="count",
                dest=arg_opts.get("dest", "log.verbosity"),
                default=arg_opts.get("default", 0),
                help=arg_opts.get("help", "Set Verbosity Level, e.g. -vv for more detail."),
            )
        # --debug
        arg_key = "--debug"
        if opts.get(arg_key) is not False:
            # The user may provide a dict with keys 'default' and/or 'help'
            arg_opts = opts.get(arg_key, {})
            g.add_argument(
                "--debug",
                action="count",
                dest=arg_opts.get("dest", "log.debug_mode"),
                default=arg_opts.get("default", 0),
                help=arg_opts.get("help", "Enable debug mode."),
            )


    @staticmethod
    def common_details(parser: argparse.ArgumentParser, opts: dict = {}) -> None:
        """
        Adds arguments specifying which entities to include/exclude.
        Only override 'default' and 'help'.
        """

        g = parser.add_argument_group("Data Granularity", "Specify which entities to include in the operation.")

        for group, default_val, help_val in [
            ("orgs", True, "Include Orgs"),
            ("users", True, "Include Users"),
            ("flows", True, "Include Flows"),
            ("triggers", True, "Include Triggers"),
            ("contacts", True, "Include Contacts"),
            ("meta", True, "Include Org Contact Metadata"),
            ("history", False, "Include Flow Run History"),
        ]:
            arg_key = f"--include-{group}"
            if opts.get(arg_key) is not False:
                arg_opts = opts.get(arg_key, {})
                g.add_argument(
                    arg_key,
                    dest=arg_opts.get("dest", f"granularity.{group}"),
                    action=argparse.BooleanOptionalAction,
                    default=arg_opts.get("default", default_val),
                    help=arg_opts.get("help", help_val),
                )


    @staticmethod
    def common_scenarios(parser: argparse.ArgumentParser, opts: dict = {}) -> None:
        """
        Adds scenario-inclusion/exclusion arguments, plus the common_details.
        Only override 'default' and 'help'.
        """

        g = parser.add_argument_group("Scenario Arguments", "Specify scenarios to include/exclude for operation..")
        # --scenario
        if opts.get("--scenario") is not False:
            arg_opts = opts.get("--scenario", {})
            g.add_argument(
                "--scenario",
                type=str,
                action="append",
                dest=arg_opts.get("dest", "scenarios.include"),
                default=arg_opts.get("default", []),  # override if present
                help=arg_opts.get("help", "Specify scenarios to include (defaults to all)."),
            )

        # --exclude-scenario
        if opts.get("--exclude-scenario") is not False:
            arg_opts = opts.get("--exclude-scenario", {})
            g.add_argument(
                "--exclude-scenario",
                type=str,
                action="append",
                dest=arg_opts.get("dest", "scenarios.exclude"),
                default=arg_opts.get("default", []),
                help=arg_opts.get("help", "Specify scenarios to exclude."),
            )

        SandboxArgParser.common_details(parser, opts)
        return parser

    @staticmethod
    def common_data_protection(parser: argparse.ArgumentParser, opts: dict = {}) -> None:
        """
        Adds arguments that control whether data is reset or overwritten.
        Only override 'default' and 'help'.
        """

        g = parser.add_argument_group("Data Protection", "Control data reset and overwrite behavior for operation.")

        # --reset
        if opts.get("--reset") is not False:
            arg_opts = opts.get("--reset", {})
            g.add_argument(
                "--reset",
                dest=arg_opts.get("dest", "data_protection.reset_existing_data"),
                action=argparse.BooleanOptionalAction,
                default=arg_opts.get("default", False),
                help=arg_opts.get("help", "Reset existing data for impacted entities."),
            )

        # --overwrite
        if opts.get("--overwrite") is not False:
            arg_opts = opts.get("--overwrite", {})
            g.add_argument(
                "--overwrite",
                dest=arg_opts.get("dest", "data_protection.overwrite_local_changes"),
                action=argparse.BooleanOptionalAction,
                default=arg_opts.get("default", False),
                help=arg_opts.get("help", "Overwrite local changes."),
            )

        # --warn
        if opts.get("--warn") is not False:
            arg_opts = opts.get("--warn", {})
            g.add_argument(
                "--warn",
                action=argparse.BooleanOptionalAction,
                dest=arg_opts.get("dest", "data_protection.warn_before_change"),
                default=arg_opts.get("default", True),
                help=arg_opts.get("help", "Warn before overwritting or deleting records."),
            )

    @staticmethod
    def common_config(parser: argparse.ArgumentParser, opts: dict = {}) -> None:
        """
        Adds arguments for configuration paths, user credentials, plus scenario logic.
        Only override 'default' and 'help'.
        """

        g = parser.add_argument_group("Data Overrides", "Override config settings")

        # --config
        if opts.get("--config") is not False:
            arg_opts = opts.get("--config", {})
            g.add_argument(
                "--config",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "config.file"),
                required=arg_opts.get("required", False),
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Path to configuration file."),
            )

        # --user
        if opts.get("--user") is not False:
            arg_opts = opts.get("--user", {})
            g.add_argument(
                "--user",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "data.root_user.account"),
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Root User (default: system username @ communityconnectlabs.com)."),
            )

        # --password
        if opts.get("--password") is not False:
            arg_opts = opts.get("--password", {})
            g.add_argument(
                "--password",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "data.root_user.password"),
                default=arg_opts.get("default", "ccl-rapidpro-root-5234"),
                help=arg_opts.get("help", "Root User Password (default: 'ccl-rapidpro-root-5234')."),
            )

        # Add scenario arguments
        SandboxArgParser.common_scenarios(parser, opts)
        return parser

    #
    # Subcommand definitions
    #
    @staticmethod
    def init_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'init' command.
        """
        parser = subparsers.add_parser("init", help="Initialize sandbox environment.")
        opts['--config'] = opts.get('--config', {'default': './temba/config/sandbox/sandbox_config.yaml'})

        SandboxArgParser.common_config(parser, opts)
        SandboxArgParser.common(parser, opts)

    @staticmethod
    def migrate_subparser__apply(migrate_subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        # update
        parser = migrate_subparsers.add_parser("apply", help="Apply migrations (default: head).")
        g = parser.add_argument_group("Migration Arguments", "Migrate to a specific version or rollback to a previous version.")
        if opts.get("--tag") is not False:
            arg_opts = opts.get("--tag", {})
            g.add_argument(
                "--tag",
                type=str,
                dest=arg_opts.get("dest", "migrate.to"),
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Version to migrate up to (default: head)."),
            )
        if opts.get("--count") is not False:
            arg_opts = opts.get("--count", {})
            g.add_argument(
                "--count",
                type=str,
                dest=arg_opts.get("dest", "migrate.count"),
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Change versions to rollback"),
            )
        SandboxArgParser.common_scenarios(parser, opts)
        SandboxArgParser.common_data_protection(parser, opts)
        SandboxArgParser.common(parser, opts)

    @staticmethod
    def migrate_subparser__rollback(migrate_subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        parser = migrate_subparsers.add_parser("rollback", help="Rollback migrations (default: last tag).")
        g = parser.add_argument_group("Migration Arguments", "Migrate to a specific version or rollback to a previous version.")
        if opts.get("--tag") is not False:
            arg_opts = opts.get("--tag", {})
            g.add_argument(
                "--tag",
                type=str,
                dest=arg_opts.get("dest", "migrate.to"),
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Version to rollback to (default: last tag)."),
            )
        if opts.get("--count") is not False:
            arg_opts = opts.get("--count", {})
            g.add_argument(
                "--count",
                type=str,
                dest=arg_opts.get("dest", "migrate.count"),
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Change versions to rollback"),
            )
        SandboxArgParser.common_scenarios(parser, opts)
        SandboxArgParser.common_data_protection(parser, opts)
        SandboxArgParser.common(parser, opts)

    @staticmethod
    def migrate_subparser__info(migrate_subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        parser = migrate_subparsers.add_parser("info", help="Migration Status")
        SandboxArgParser.common(parser, opts)

    @staticmethod
    def migrate_subparser__tag(migrate_subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        parser = migrate_subparsers.add_parser("tag", help="add/remove or list tags")
        g = parser.add_argument_group("Migration Tag Arguments", "Add, remove, or list tags.")

        #parser = subparsers.add_parser("add", help="Add named tag to current migration point")
        if opts.get("migrate.tag") is not False:
            arg_opts = opts.get("migrate.tag", {})
            g.add_argument(
                "migrate.tag",
                default="*list*",
                nargs=argparse.OPTIONAL,
                type=str,
                action="store",
                help=arg_opts.get("help", "Tag name. Leave blank to list tags"),
            )

        if opts.get("--force") is not False:
            arg_opts = opts.get("--force", {})
            g.add_argument(
                "--force",
                action=argparse.BooleanOptionalAction,
                dest=arg_opts.get("dest", "migrate.force"),
                default=arg_opts.get("default", True),
                help=arg_opts.get("help", "Overwrite existing tag."),
            )
        if opts.get("--delete") is not False:
            arg_opts = opts.get("--delete", {})
            g.add_argument(
                "-d",
                "--delete",
                action=argparse.BooleanOptionalAction,
                dest=arg_opts.get("dest", "migrate.delete"),
                default=arg_opts.get("default", False),
                help=arg_opts.get("help", "Delete Tag."),
            )
        if opts.get("--filter") is not False:
            arg_opts = opts.get("--filter", {})
            g.add_argument(
                "--filter",
                dest=arg_opts.get("dest", "migrate.filter.tags"),
                type=str,
                action="append",
                help=arg_opts.get("help", "Filter listed tags by globs."),
                default=arg_opts.get("default", [])
            )

    @staticmethod
    def migrate_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'migrate' command, which includes 'update' and 'rollback' actions.
        """
        parser = subparsers.add_parser("migrate", help="Migrate sandbox configurations.")
        migrate_subparsers = parser.add_subparsers(
            dest="action",
            title="migrate command",
            help="migrate action",
            required=True
        )
        SandboxArgParser.migrate_subparser__apply(migrate_subparsers, opts)
        SandboxArgParser.migrate_subparser__rollback(migrate_subparsers, opts)
        SandboxArgParser.migrate_subparser__info(migrate_subparsers, opts)
        SandboxArgParser.migrate_subparser__tag(migrate_subparsers, opts)

        # rollback


    @staticmethod
    def reset_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'reset' command.
        """
        parser = subparsers.add_parser("reset", help="Reset sandbox configurations.")
        SandboxArgParser.common_config(parser, opts)
        SandboxArgParser.common_data_protection(parser, opts)
        SandboxArgParser.common(parser, opts)


    @staticmethod
    def export_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'export' command.
        """
        parser = subparsers.add_parser("export", help="Export sandbox current data.")
        if opts.get("--file") is not False:
            arg_opts = opts.get("--file", {})
            parser.add_argument(
                "--file",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "export.file"),
                default=arg_opts.get("default", "./sandbox_export.yaml"),
                help=arg_opts.get("help", "Destination file for export."),
            )
        SandboxArgParser.common_scenarios(parser, opts)
        SandboxArgParser.common(parser, opts)


    @staticmethod
    def import_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'import' command.
        """
        parser = subparsers.add_parser("import", help="Import sandbox scenarios.")
        if opts.get("--file") is not False:
            arg_opts = opts.get("--file", {})
            parser.add_argument(
                "--file",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "import.file"),
                default=arg_opts.get("default", None),
                required=True,
                help=arg_opts.get("help", "Source file for import."),
            )
        SandboxArgParser.common_data_protection(parser, opts)
        SandboxArgParser.common_scenarios(parser, opts)
        SandboxArgParser.common(parser, opts)


    @staticmethod
    def status_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'status' command.
        """
        parser = subparsers.add_parser("status", help="Show sandbox status.")
        SandboxArgParser.common(parser, opts)


    @staticmethod
    def list_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'list' command.
        """
        parser = subparsers.add_parser("list", help="List sandbox configurations.")
        SandboxArgParser.common(parser, opts)


    @staticmethod
    def validate_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'validate' command.
        """
        parser = subparsers.add_parser("validate", help="Validate sandbox data integrity.")
        SandboxArgParser.common_scenarios(parser, opts)
        SandboxArgParser.common(parser, opts)


    @staticmethod
    def report_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'report' command.
        """
        parser = subparsers.add_parser("report", help="Generate sandbox data integrity report.")
        SandboxArgParser.common_scenarios(parser, opts)
        SandboxArgParser.common(parser, opts)


    @staticmethod
    def config_subparser__save(config_subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        parser = config_subparsers.add_parser("save", help="Save sandbox configurations.")
        if opts.get("--file") is not False:
            arg_opts = opts.get("--file", {})
            parser.add_argument(
                "--file",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "config.save_file"),
                default=arg_opts.get("default", "./sandbox_config.yaml"),
                help=arg_opts.get("help", "Destination file for saving configurations."),
            )
        SandboxArgParser.common_scenarios(parser, opts)
        SandboxArgParser.common(parser, opts)


    @staticmethod
    def config_subparser__edit(config_subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        parser = config_subparsers.add_parser("edit", help="Edit sandbox configurations.")
        if opts.get("--config") is not False:
            arg_opts = opts.get("--config", {})
            parser.add_argument(
                "--config",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "config.edit_file"),
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Configuration file to edit."),
            )
        if opts.get("--interactive") is not False:
            arg_opts = opts.get("--interactive", {})
            parser.add_argument(
                "--interactive",
                action=argparse.BooleanOptionalAction,
                default=arg_opts.get("default", True),
                help=arg_opts.get("help", "Enable interactive mode."),
            )
        opts['--config'] = False
        SandboxArgParser.common_config(parser, opts)
        SandboxArgParser.common(parser, opts)





    @staticmethod
    def config_subparser__load(config_subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        parser = config_subparsers.add_parser("load", help="Load sandbox configurations.")
        if opts.get("--config") is not False:
            arg_opts = opts.get("--config", {})
            parser.add_argument(
                "--config",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "config.load_file"),
                required=True,
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Configuration file to load."),
            )
        if opts.get("--apply") is not False:
            arg_opts = opts.get("--apply", {})
            parser.add_argument(
                "--apply",
                dest=arg_opts.get("dest", "config.apply_on_load"),
                action=argparse.BooleanOptionalAction,
                default=arg_opts.get("default", False),
                help=arg_opts.get("help", "Apply configuration as soon as loaded."),
            )
        opts['--config'] = False
        SandboxArgParser.common_data_protection(parser, opts)
        SandboxArgParser.common_config(parser, opts)
        SandboxArgParser.common(parser, opts)

    @staticmethod
    def config_subparser__show(config_subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        parser = config_subparsers.add_parser("show", help="View sandbox configurations.")
        if opts.get("--config") is not False:
            arg_opts = opts.get("--file", {})
            parser.add_argument(
                "--config",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "config.view_file"),
                required=True,
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Configuration file to view."),
            )
        opts['--config'] = False
        SandboxArgParser.common_config(parser, opts)
        SandboxArgParser.common(parser, opts)



    @staticmethod
    def config_subparser__new(config_subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        parser = config_subparsers.add_parser("new", help="Edit sandbox configurations.")
        if opts.get("--file") is not False:
            arg_opts = opts.get("--file", {})
            parser.add_argument(
                "--file",
                type=str,
                action="store",
                dest=arg_opts.get("dest", "config.new_file"),
                default=arg_opts.get("default", "./sandbox_config.yaml"),
                help=arg_opts.get("help", "Configuration file to create."),
            )
        if opts.get("--template") is not False:
            arg_opts = opts.get("--template", {})
            parser.add_argument(
                "--template",
                type=str,
                action='store',
                dest=arg_opts.get("dest", "config.template"),
                default=arg_opts.get("default", None),
                help=arg_opts.get("help", "Config template to start from"),
            )
        if opts.get("--interactive") is not False:
            arg_opts = opts.get("--interactive", {})
            parser.add_argument(
                "--interactive",
                action=argparse.BooleanOptionalAction,
                dest=arg_opts.get("dest", "config.interactive"),
                default=arg_opts.get("default", True),
                help=arg_opts.get("help", "Enable interactive mode."),
            )
        opts['--config'] = False
        SandboxArgParser.common_config(parser, opts)
        SandboxArgParser.common(parser, opts)

    @staticmethod
    def config_subparser(subparsers: argparse._SubParsersAction, opts: dict = {}) -> None:
        """
        Subparser for 'config' command with sub-subcommands: save, edit, load.
        (Following the original code's naming/structure.)
        """
        parser = subparsers.add_parser("config", help="Sandbox Config")
        config_subparsers = parser.add_subparsers(
            dest="action",
            title="config command",
            help="Config Action like save, edit, load, show, new",
            required=True
        )
        SandboxArgParser.config_subparser__save(config_subparsers, opts)
        SandboxArgParser.config_subparser__edit(config_subparsers, opts)
        SandboxArgParser.config_subparser__load(config_subparsers, opts)
        SandboxArgParser.config_subparser__show(config_subparsers, opts)
        SandboxArgParser.config_subparser__new(config_subparsers, opts)


    @staticmethod
    def build_parser(parser: argparse.ArgumentParser, opts: dict = {}) -> argparse.ArgumentParser:
        """
        Build the main parser by attaching all commands.
        Pass `opts` to each subparser method.
        """
        subparsers = parser.add_subparsers(
            dest="command",
            title="command",
            help="sandbox_setup command",
            required=True
        )

        SandboxArgParser.init_subparser(subparsers, opts)
        SandboxArgParser.migrate_subparser(subparsers, opts)
        SandboxArgParser.reset_subparser(subparsers, opts)
        SandboxArgParser.export_subparser(subparsers, opts)
        SandboxArgParser.import_subparser(subparsers, opts)
        SandboxArgParser.status_subparser(subparsers, opts)
        SandboxArgParser.list_subparser(subparsers, opts)
        SandboxArgParser.config_subparser(subparsers, opts)
        SandboxArgParser.validate_subparser(subparsers, opts)
        SandboxArgParser.report_subparser(subparsers, opts)

        return parser

class Command(BaseCommand):
    UPDATED__
    help = "Sandbox Setup Command"

    def add_arguments(self, parser):
        """
        Override Django's add_arguments to build out our subcommands via SandboxArgParser.
        """
        SandboxArgParser.build_parser(parser)

    def handle(self, *args, **options):
        """
        Handle the command after arguments are parsed.
        """

        if options['command'] == 'init':
            self.handle_init(args, options)
        elif options['command'] == 'migrate':
            self.handle_migrate(args, options)
        elif options['command'] == 'reset':
            self.handle_reset(args, options)
        elif options['command'] == 'export':
            self.handle_export(args, options)
        elif options['command'] == 'import':
            self.handle_import(args, options)
        elif options['command'] == 'status':
            self.handle_status(args, options)
        elif options['command'] == 'list':
            self.handle_list(args, options)
        elif options['command'] == 'config':
            self.handle_config(args, options)
        elif options['command'] == 'validate':
            self.handle_validate(args, options)
        elif options['command'] == 'report':
            self.handle_report(args, options)
        else:
            self.log_handle(heading = "ERROR", args=args, options=options)
            raise CommandError(f"Invalid command: {options['command']}")

        # Normally, you'd dispatch logic here based on `options['subcommand']`,
        # plus any sub-subcommand or further arguments.

    def prepare_options(self, args, options):
        x = {k: v for k, v in options.items() if k not in ['settings', 'pythonpath', 'traceback', 'no_color', 'force_color', 'skip_checks', 'verbosity']}
        o = {}
        for k, v in x.items():
            pointer = o
            path = k.split('.')
            for position in path[:-1]:
                pointer[position] = pointer.get(position, {})
                pointer = pointer[position]
            pointer[path[-1]] = v
        return o

    def log_handle(self, heading, args: tuple = (), options: dict = {}) -> None:
        if options.get('action'):
            template = "\n[{heading}] ... {command} {action}\n--- args\n{args}\n--- options\n{options}\n\n"
        else:
            template = "\n[{heading}] ... {command}\n--- args\n{args}\n--- options\n{options}\n\n"
        self._log(template.format(
            heading=heading,
            command=options.get('command'),
            action=options.get('action'),
            args=args,
            options=json.dumps(options,indent=4, sort_keys=True))
        )

    def handle_init(self, args, options):
        """
        Handle the 'init' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "INIT", args=args, options=options)

    def handle_migrate(self, args, options):
        """
        Handle the 'migrate' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "MIGRATE", args=args, options=options)

    def handle_reset(self, args, options):
        """
        Handle the 'reset' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "RESET", args=args, options=options)

    def handle_export(self, args, options):
        """
        Handle the 'export' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "EXPORT", args=args, options=options)

    def handle_import(self, args, options):
        """
        Handle the 'import' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "IMPORT", args=args, options=options)

    def handle_status(self, args, options):
        """
        Handle the 'status' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "STATUS", args=args, options=options)


    def handle_list(self, args, options):
        """
        Handle the 'list' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "LIST", args=args, options=options)


    def handle_config(self, args, options):
        """
        Handle the 'config' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "CONFIG", args=args, options=options)


    def handle_validate(self, args, options):
        """
        Handle the 'validate' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "VALIDATE", args=args, options=options)


    def handle_report(self, args, options):
        """
        Handle the 'report' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading = "REPORT", args=args, options=options)

    def _log(self, text):
        self.stdout.write(text, ending="")
        self.stdout.flush()
