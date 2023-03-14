from temba.utils.celery import nonoverlapping_task

from temba.ccl.sdoh.models import SDOHDumpTask


@nonoverlapping_task(track_started=True, name="dump_sdoh_data_task")
def dump_sdoh_data_task():
    SDOHDumpTask.run_task()
