import logging

from celery import shared_task

from temba.flows.models import Flow

from .models import ExportLinksTask, Link, LinkContacts

logger = logging.getLogger(__name__)


@shared_task(track_started=True, name="export_link_task")
def export_link_task(id):
    """
    Export link contacts to a file and e-mail a link to the user
    """
    ExportLinksTask.objects.get(id=id).perform()


@shared_task(track_started=True, name="handle_link_task")
def handle_link_task(link_id, contact_id, related_flow_uuid=None):
    link = Link.objects.filter(pk=link_id).only("created_by", "modified_by").first()
    related_flow = Flow.objects.filter(uuid=related_flow_uuid).first() if related_flow_uuid else None
    if link and contact_id:
        LinkContacts.objects.create(
            link_id=link.id,
            contact_id=contact_id,
            created_by=link.created_by,
            modified_by=link.modified_by,
            flow=related_flow,
        )
