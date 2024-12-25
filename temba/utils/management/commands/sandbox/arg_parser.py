import argparse


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

        # parser = subparsers.add_parser("add", help="Add named tag to current migration point")
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
