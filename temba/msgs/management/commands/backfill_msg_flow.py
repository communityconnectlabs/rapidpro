from django.core.management import BaseCommand, CommandError

from temba.msgs.tasks import backfill_msg_flow


class Command(BaseCommand):  # pragma: no cover
    def add_arguments(self, parser):
        parser.add_argument(
            "--orgs",
            type=str,
            action="store",
            dest="orgs",
            default=None,
            help="comma separated list of IDs of orgs to populate the Msg flow field",
        )

    def handle(self, *args, **options):
        org_list = options["orgs"]
        if org_list is not None:
            orgs = org_list.split(",")
        else:
            raise CommandError("Kindly provide at least one org ID")

        for org_id in orgs:
            backfill_msg_flow.delay(int(org_id))
