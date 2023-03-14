from django.core.management import BaseCommand
from ...tasks import dump_sdoh_data_task


class Command(BaseCommand):
    help = "Dump of SDoH data for the USA"

    def add_arguments(self, parser):
        parser.add_argument("--async", action="store_true", help="Run as Celery task")

    def handle(self, *args, **options):
        if options.get("async"):
            dump_sdoh_data_task.delay()
        else:
            dump_sdoh_data_task()
