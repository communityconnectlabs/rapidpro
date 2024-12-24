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


class SandboxArgParser:
    """
    A helper class that organizes all argument definitions for the sandbox_setup command.
    """

    @staticmethod
    def common(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """
        Adds common arguments used across multiple subcommands.
        """
        parser.add_argument(
            "-v",
            "--verbose",
            action="count",
            default=0,
            help="Set Verbosity Level, e.g. -vv for more detail."
        )
        return parser

    @staticmethod
    def common_details(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """
        Adds arguments that specify which entities to include/exclude.
        """
        parser.add_argument(
            "--include-orgs",
            action=argparse.BooleanOptionalAction,
            help="Include Orgs.",
            default=True
        )
        parser.add_argument(
            "--include-users",
            action=argparse.BooleanOptionalAction,
            help="Include Users.",
            default=True
        )
        parser.add_argument(
            "--include-flows",
            action=argparse.BooleanOptionalAction,
            help="Include Flows.",
            default=True
        )
        parser.add_argument(
            "--include-triggers",
            action=argparse.BooleanOptionalAction,
            help="Include Triggers.",
            default=True
        )
        parser.add_argument(
            "--include-contacts",
            action=argparse.BooleanOptionalAction,
            help="Include Org Contacts.",
            default=True
        )
        parser.add_argument(
            "--include-meta",
            action=argparse.BooleanOptionalAction,
            help="Include Org Contact Metadata.",
            default=True
        )
        parser.add_argument(
            "--include-history",
            action=argparse.BooleanOptionalAction,
            help="Include Flow Run History.",
            default=False
        )
        return parser

    @staticmethod
    def common_scenarios(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """
        Adds scenario-inclusion/exclusion arguments, plus the common details.
        """
        parser.add_argument(
            "--scenario",
            type=str,
            action="append",
            dest="scenarios",
            help="Specify specific scenarios to include (defaults to all)."
        )
        parser.add_argument(
            "--exclude-scenario",
            type=str,
            action="append",
            dest="exclude_scenarios",
            help="Specify specific scenarios to exclude."
        )
        SandboxArgParser.common_details(parser)
        return parser

    @staticmethod
    def common_data_protection(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """
        Adds arguments that control whether data is reset or overwritten.
        """
        parser.add_argument(
            "--reset",
            action=argparse.BooleanOptionalAction,
            help="Reset existing data for impacted entities.",
            default=False
        )
        parser.add_argument(
            "--overwrite",
            action=argparse.BooleanOptionalAction,
            help="Overwrite local changes.",
            default=False
        )
        return parser

    @staticmethod
    def common_config(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """
        Adds arguments for configuration paths, user credentials, plus scenario logic.
        """
        parser.add_argument(
            "--config",
            type=str,
            action="store",
            dest="config",
            help="Path to configuration file."
        )
        parser.add_argument(
            "--user",
            type=str,
            action="store",
            dest="user",
            help="Root User (default: system username @ communityconnectlabs.com)."
        )
        parser.add_argument(
            "--password",
            type=str,
            action="store",
            dest="password",
            default="ccl-rapidpro-root-5234",
            help="Root User Password (default: 'ccl-rapidpro-root-5234')."
        )
        SandboxArgParser.common_scenarios(parser)
        return parser

    #
    # Subcommand Definitions
    #

    @staticmethod
    def init_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'init' command.
        """
        parser = subparsers.add_parser(
            "init",
            help="Initialize sandbox environment."
        )
        SandboxArgParser.common_config(parser)
        SandboxArgParser.common(parser)

    @staticmethod
    def migrate_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'migrate' command, which includes 'update' and 'rollback' actions.
        """
        parser = subparsers.add_parser(
            "migrate",
            help="Migrate sandbox configurations."
        )

        # Migrate sub-subparsers
        migrate_subparsers = parser.add_subparsers(
            dest="action",
            title="migrate command",
            help="update | rollback",
            required=True
        )

        # update
        update_parser = migrate_subparsers.add_parser(
            "update",
            help="Apply migrations to a specific version (or head)."
        )
        update_parser.add_argument(
            "--tag",
            type=str,
            action="store",
            dest="tag",
            help="Version to migrate up to (default: head)."
        )
        SandboxArgParser.common_scenarios(update_parser)
        SandboxArgParser.common_data_protection(update_parser)
        SandboxArgParser.common(update_parser)

        # rollback
        rollback_parser = migrate_subparsers.add_parser(
            "rollback",
            help="Rollback migrations to a previous version."
        )
        rollback_parser.add_argument(
            "--tag",
            type=str,
            action="store",
            dest="tag",
            help="Version to rollback to (default: last tag)."
        )
        SandboxArgParser.common_scenarios(rollback_parser)
        SandboxArgParser.common_data_protection(rollback_parser)
        SandboxArgParser.common(rollback_parser)

    @staticmethod
    def reset_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'reset' command.
        """
        parser = subparsers.add_parser(
            "reset",
            help="Reset sandbox configurations."
        )
        SandboxArgParser.common_config(parser)
        SandboxArgParser.common_data_protection(parser)
        SandboxArgParser.common(parser)

    @staticmethod
    def export_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'export' command.
        """
        parser = subparsers.add_parser(
            "export",
            help="Export sandbox current data."
        )
        parser.add_argument(
            "--file",
            type=str,
            action="store",
            dest="config",
            help="Destination file for export."
        )
        SandboxArgParser.common_scenarios(parser)
        SandboxArgParser.common(parser)

    @staticmethod
    def import_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'import' command.
        """
        parser = subparsers.add_parser(
            "import",
            help="Import sandbox scenarios."
        )
        parser.add_argument(
            "--file",
            type=str,
            action="store",
            dest="config",
            help="Source file for import."
        )
        SandboxArgParser.common_data_protection(parser)
        SandboxArgParser.common_scenarios(parser)
        SandboxArgParser.common(parser)

    @staticmethod
    def status_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'status' command.
        """
        parser = subparsers.add_parser(
            "status",
            help="Show sandbox status."
        )
        SandboxArgParser.common(parser)

    @staticmethod
    def list_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'list' command.
        """
        parser = subparsers.add_parser(
            "list",
            help="List sandbox configurations."
        )
        SandboxArgParser.common(parser)

    @staticmethod
    def validate_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'validate' command.
        """
        parser = subparsers.add_parser(
            "validate",
            help="Validate sandbox data integrity."
        )
        SandboxArgParser.common_scenarios(parser)
        SandboxArgParser.common(parser)

    @staticmethod
    def report_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'report' command.
        """
        parser = subparsers.add_parser(
            "report",
            help="Generate sandbox data integrity report."
        )
        SandboxArgParser.common_scenarios(parser)
        SandboxArgParser.common(parser)

    @staticmethod
    def config_subparser(subparsers: argparse._SubParsersAction) -> None:
        """
        Subparser for 'config' command with sub-subcommands: save, edit, load.
        """
        # Top-level 'save' parser (it’s a bit unusual that we name the top-level parser "save"
        # but we’ll follow the original code’s structure):
        parser = subparsers.add_parser(
            "save",
            help="Sandbox Config."
        )

        config_subparsers = parser.add_subparsers(
            dest="config",
            title="config command",
            help="save | edit | load",
            required=True
        )

        # save
        save_parser = config_subparsers.add_parser(
            "save",
            help="Save sandbox configurations."
        )
        save_parser.add_argument(
            "--file",
            type=str,
            action="store",
            dest="config",
            help="Destination file for saving configurations."
        )
        SandboxArgParser.common_scenarios(save_parser)
        SandboxArgParser.common(save_parser)

        # edit
        edit_parser = config_subparsers.add_parser(
            "edit",
            help="Edit sandbox configurations."
        )
        edit_parser.add_argument(
            "--file",
            type=str,
            action="store",
            dest="config",
            help="Configuration file to edit."
        )
        edit_parser.add_argument(
            "--interactive",
            action=argparse.BooleanOptionalAction,
            help="Enable interactive mode.",
            default=True
        )
        SandboxArgParser.common_scenarios(edit_parser)
        SandboxArgParser.common(edit_parser)

        # load
        load_parser = config_subparsers.add_parser(
            "load",
            help="Load sandbox configurations."
        )
        load_parser.add_argument(
            "--file",
            type=str,
            action="store",
            dest="config",
            help="Configuration file to load."
        )
        load_parser.add_argument(
            "--apply",
            action=argparse.BooleanOptionalAction,
            help="Apply configuration as soon as loaded.",
            default=False
        )
        SandboxArgParser.common_data_protection(load_parser)
        SandboxArgParser.common_scenarios(load_parser)
        SandboxArgParser.common(load_parser)

    @staticmethod
    def build_parser(parser: argparse.ArgumentParser) -> argparse.ArgumentParser:
        """
        Build the main parser by attaching all subcommands.
        """
        subparsers = parser.add_subparsers(
            dest="subcommand",
            title="subcommands",
            help="init | migrate | reset | export | import | status | list | config | validate | report",
            required=True
        )

        # Register each subcommand
        SandboxArgParser.init_subparser(subparsers)
        SandboxArgParser.migrate_subparser(subparsers)
        SandboxArgParser.reset_subparser(subparsers)
        SandboxArgParser.export_subparser(subparsers)
        SandboxArgParser.import_subparser(subparsers)
        SandboxArgParser.status_subparser(subparsers)
        SandboxArgParser.list_subparser(subparsers)
        SandboxArgParser.config_subparser(subparsers)
        SandboxArgParser.validate_subparser(subparsers)
        SandboxArgParser.report_subparser(subparsers)

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
        self._log(f"Received options: {options}\n")

        # Normally, you'd dispatch logic here based on `options['subcommand']`,
        # plus any sub-subcommand or further arguments.

    def _log(self, text):
        self.stdout.write(text, ending="")
        self.stdout.flush()