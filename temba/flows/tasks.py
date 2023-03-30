import os
import logging
import re
import time
import boto3

from datetime import datetime, timedelta

import pytz
import requests
from django_redis import get_redis_connection

from django.conf import settings
from django.db.models import F
from django.utils import timezone
from django.utils.timesince import timesince

from celery import shared_task
from sorl.thumbnail import get_thumbnail

from temba.utils import chunk_list
from temba.utils.celery import nonoverlapping_task

from .models import (
    Flow,
    ExportFlowImagesTask,
    ExportFlowResultsTask,
    FlowCategoryCount,
    FlowNodeCount,
    FlowPathCount,
    FlowRevision,
    FlowRun,
    FlowRunCount,
    FlowSession,
    FlowStart,
    FlowStartCount,
    FlowImage,
    MergeFlowsTask,
)

FLOW_TIMEOUT_KEY = "flow_timeouts_%y_%m_%d"
logger = logging.getLogger(__name__)


@shared_task(track_started=True, name="update_session_wait_expires")
def update_session_wait_expires(flow_id):
    """
    Update the wait_expires_on of any session currently waiting in the given flow
    """

    flow = Flow.objects.get(id=flow_id)
    session_ids = flow.sessions.filter(status=FlowSession.STATUS_WAITING).values_list("id", flat=True)

    for id_batch in chunk_list(session_ids, 1000):
        batch = FlowSession.objects.filter(id__in=id_batch)
        batch.update(wait_expires_on=F("wait_started_on") + timedelta(minutes=flow.expires_after_minutes))


@shared_task(track_started=True, name="export_flow_results_task")
def export_flow_results_task(export_id):
    """
    Export a flow to a file and e-mail a link to the user
    """
    ExportFlowResultsTask.objects.select_related("org", "created_by").get(id=export_id).perform()


@shared_task(track_started=True, name="download_flow_images_task")
def download_flow_images_task(id):
    """
    Download flow images to a zip file and e-mail a link to the user
    """
    export_task = ExportFlowImagesTask.objects.filter(pk=id).first()
    if export_task:
        export_task.perform()


@nonoverlapping_task(track_started=True, name="squash_flowcounts", lock_timeout=7200)
def squash_flowcounts():
    FlowNodeCount.squash()
    FlowRunCount.squash()
    FlowCategoryCount.squash()
    FlowStartCount.squash()
    FlowPathCount.squash()


@nonoverlapping_task(track_started=True, name="trim_flow_revisions")
def trim_flow_revisions():
    start = timezone.now()

    # get when the last time we trimmed was
    r = get_redis_connection()
    last_trim = r.get(FlowRevision.LAST_TRIM_KEY)
    if not last_trim:
        last_trim = 0

    last_trim = datetime.utcfromtimestamp(int(last_trim)).astimezone(pytz.utc)
    count = FlowRevision.trim(last_trim)

    r.set(FlowRevision.LAST_TRIM_KEY, int(timezone.now().timestamp()))

    elapsed = timesince(start)
    logger.info(f"Trimmed {count} flow revisions since {last_trim} in {elapsed}")


@nonoverlapping_task(track_started=True, name="trim_flow_sessions_and_starts")
def trim_flow_sessions_and_starts():
    trim_flow_sessions()
    trim_flow_starts()


def trim_flow_sessions():
    """
    Cleanup old flow sessions
    """
    trim_before = timezone.now() - settings.RETENTION_PERIODS["flowsession"]
    num_deleted = 0
    start = timezone.now()

    logger.info(f"Deleting flow sessions which ended before {trim_before.isoformat()}...")

    while True:
        session_ids = list(FlowSession.objects.filter(ended_on__lte=trim_before).values_list("id", flat=True)[:1000])
        if not session_ids:
            break

        # detach any flows runs that belong to these sessions
        FlowRun.objects.filter(session_id__in=session_ids).update(session_id=None)

        FlowSession.objects.filter(id__in=session_ids).delete()
        num_deleted += len(session_ids)

        if num_deleted % 10000 == 0:  # pragma: no cover
            print(f" > Deleted {num_deleted} flow sessions")

    logger.info(f"Deleted {num_deleted} flow sessions in {timesince(start)}")


def trim_flow_starts():
    """
    Cleanup completed non-user created flow starts
    """
    trim_before = timezone.now() - settings.RETENTION_PERIODS["flowstart"]
    num_deleted = 0
    start = timezone.now()

    logger.info(f"Deleting completed non-user created flow starts created before {trim_before.isoformat()}")

    while True:
        start_ids = list(
            FlowStart.objects.filter(
                created_by=None,
                status__in=(FlowStart.STATUS_COMPLETE, FlowStart.STATUS_FAILED),
                modified_on__lte=trim_before,
            ).values_list("id", flat=True)[:1000]
        )
        if not start_ids:
            break

        # detach any flows runs that belong to these starts
        run_ids = FlowRun.objects.filter(start_id__in=start_ids).values_list("id", flat=True)[:100000]
        while len(run_ids) > 0:
            for chunk in chunk_list(run_ids, 1000):
                FlowRun.objects.filter(id__in=chunk).update(start_id=None)

            # reselect for our next batch
            run_ids = FlowRun.objects.filter(start_id__in=start_ids).values_list("id", flat=True)[:100000]

        FlowStart.contacts.through.objects.filter(flowstart_id__in=start_ids).delete()
        FlowStart.groups.through.objects.filter(flowstart_id__in=start_ids).delete()
        FlowStartCount.objects.filter(start_id__in=start_ids).delete()
        FlowStart.objects.filter(id__in=start_ids).delete()
        num_deleted += len(start_ids)

        if num_deleted % 10000 == 0:  # pragma: no cover
            logger.debug(f" > Deleted {num_deleted} flow starts")

    logger.info(f"Deleted {num_deleted} completed non-user created flow starts in {timesince(start)}")


@shared_task(track_started=True, name="delete_flowimage_downloaded_files")
def delete_flowimage_downloaded_files():
    print("> Running garbage collector for Flow Images zip files")
    counter_files = 0
    start = time.time()

    s3 = (
        boto3.resource(
            "s3", aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )
        if settings.DEFAULT_FILE_STORAGE == "storages.backends.s3boto3.S3Boto3Storage"
        else None
    )
    download_tasks = (
        ExportFlowImagesTask.objects.filter(cleaned=False, file_downloaded=True).only("id").order_by("created_on")
    )
    for item in download_tasks:
        try:
            file_path = item.file_path
            if s3 and settings.AWS_BUCKET_DOMAIN in file_path:
                key = file_path.replace("https://%s/" % settings.AWS_BUCKET_DOMAIN, "")
                obj = s3.Object(settings.AWS_STORAGE_BUCKET_NAME, key)
                obj.delete()
            else:
                expected_fpath = file_path.replace(settings.MEDIA_URL, "")
                file_path = os.path.join(settings.MEDIA_ROOT, expected_fpath)
                os.remove(file_path)
            item.cleaned = True
            item.save(update_fields=["cleaned"])
            counter_files += 1
        except Exception:
            pass
    print("> Garbage collection finished in %0.3fs for %s file(s)" % (time.time() - start, counter_files))


@nonoverlapping_task(track_started=True, name="generate_missing_gif_thumbnails")
def create_flow_image_thumbnail():
    images = FlowImage.objects.filter(path__endswith=".gif", path_thumbnail__isnull=True)
    for image in images:
        image_thumbnail = get_thumbnail(image.path, "50x50", crop="center", quality=99, format="PNG")
        image.path_thumbnail = image_thumbnail.url
        image.save(update_fields=["path_thumbnail"])


def merge_flow_failed(self, exc, task_id, args, kwargs, einfo):
    task_ = MergeFlowsTask.objects.filter(uuid=args[0]).first()
    if task_:
        task_.status = MergeFlowsTask.STATUS_FAILED
        task_.save()


@shared_task(track_started=True, name="merge_flows", on_failure=merge_flow_failed)
def merge_flows_task(uuid):
    task_ = MergeFlowsTask.objects.filter(uuid=uuid).first()
    if task_:
        task_.process_merging()


@nonoverlapping_task(track_started=True, name="start_active_merge_flows")
def start_active_merge_flows():
    for task_ in MergeFlowsTask.objects.filter(status=MergeFlowsTask.STATUS_ACTIVE):
        task_.process_merging()


@nonoverlapping_task(name="validate_flow_links")
def validate_flow_links(flow_id):
    try:
        # mark that flow links are being validated
        flow = Flow.objects.get(id=flow_id)
        definition = flow.get_definition()
        flow.metadata["validated_links"] = {
            "revision": definition.get("revision", 0),
            "validating": True,
        }
        flow.save(update_fields=["metadata"])

        # validating the flow links
        links = []
        for node in definition.get("nodes", []):
            for action in node.get("actions", []):
                if action["type"] == "send_msg":
                    text = action["text"]
                    action_links = re.findall(r"(https?://[^\s]+)", text)
                    links.extend(
                        [
                            {
                                "node_uuid": node["uuid"],
                                "action_uuid": action["uuid"],
                                "link": link,
                            }
                            for link in action_links
                        ]
                    )
        for link in links:
            link["status_code"] = 400
            try:
                response = requests.get(link["link"])
                link["status_code"] = response.status_code
                if not response.ok:
                    link["error"] = response.reason
            except (requests.ConnectionError, requests.HTTPError, requests.RequestException) as e:
                link["error"] = e.__doc__

        # saving the validation result
        flow.metadata["validated_links"] = {
            "processed_on": timezone.now(),
            "revision": definition.get("revision", 0),
            "links": links,
        }
        flow.save(update_fields=["metadata"])
    except Flow.DoesNotExist:
        pass
