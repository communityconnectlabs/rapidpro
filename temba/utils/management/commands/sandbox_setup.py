import json

from django.core.management import BaseCommand
from django.core.management import CommandError

from temba.sandbox import SandboxConfig, SandboxVersionControl
from .sandbox import SandboxArgParser

class Command(BaseCommand):
    """
    # Sandbox Configuration Tool for RapidPro

    ## Description

    The `sandbox_setup` command is a dev/sandbox fixture management tool designed to extend RapidPro’s capabilities. By combining the functionality of test databases with enhanced configurability and version control, the tool will:

    1. Enable resetting organizations, users, and flows to initial predefined states.
    2. Standardize UUIDs across environments to ensure consistency.
    3. Allow data migration for sandbox flows, organizations, and configurations, keeping them current through version tracking and a lightweight Erlang-style `code_up` methodology for upgrades.
    4. Offer configurable and optional components, allowing granular control over which organizations, flows, and related entities are included in the sandbox environment.
    5. Apply database changes without requiring a full flush, enabling faster iteration cycles.

    @see [Sandbox Setup Command](./sandbox_setup.md)
    """

    help = "Sandbox Setup Command - @see [Sandbox Setup](./sandbox_setup.md)"

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
            self.log_handle(heading="ERROR", args=args, options=options)
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
            options=json.dumps(options, indent=4, sort_keys=True))
        )

    def handle_init(self, args, options):
        """
        Handle the 'init' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="INIT", args=args, options=options)

    def handle_migrate(self, args, options):
        """
        Handle the 'migrate' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="MIGRATE", args=args, options=options)

    def handle_reset(self, args, options):
        """
        Handle the 'reset' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="RESET", args=args, options=options)

    def handle_export(self, args, options):
        """
        Handle the 'export' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="EXPORT", args=args, options=options)

    def handle_import(self, args, options):
        """
        Handle the 'import' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="IMPORT", args=args, options=options)

    def handle_status(self, args, options):
        """
        Handle the 'status' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="STATUS", args=args, options=options)

    def handle_list(self, args, options):
        """
        Handle the 'list' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="LIST", args=args, options=options)

    def handle_config(self, args, options):
        """
        Handle the 'config' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="CONFIG", args=args, options=options)

    def handle_validate(self, args, options):
        """
        Handle the 'validate' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="VALIDATE", args=args, options=options)

    def handle_report(self, args, options):
        """
        Handle the 'report' subcommand.
        """
        options = self.prepare_options(args, options)
        self.log_handle(heading="REPORT", args=args, options=options)

    def _log(self, text):
        self.stdout.write(text, ending="")
        self.stdout.flush()
