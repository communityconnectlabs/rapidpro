import logging
from datetime import timedelta
from urllib.parse import urlencode

from django_redis import get_redis_connection

from django.conf import settings
from django.core.mail import send_mail
from django.db.models import Case, Count, IntegerField, Q, When
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.translation import gettext_lazy as _

from celery import shared_task

from temba.flows.models import FlowRun
from temba.utils import analytics, chunk_list
from temba.utils.celery import nonoverlapping_task

from .models import (
    Broadcast,
    BroadcastMsgCount,
    Conversation,
    ConversationOwner,
    ExportMessagesTask,
    LabelCount,
    Msg,
    SystemLabel,
    SystemLabelCount,
)

logger = logging.getLogger(__name__)


@shared_task(track_started=True, name="send_to_flow_node")
def send_to_flow_node(org_id, user_id, text, **kwargs):
    from django.contrib.auth.models import User

    from temba.contacts.models import Contact
    from temba.flows.models import FlowRun
    from temba.orgs.models import Org

    org = Org.objects.get(pk=org_id)
    user = User.objects.get(pk=user_id)
    node_uuid = kwargs.get("s", None)

    runs = FlowRun.objects.filter(
        org=org, current_node_uuid=node_uuid, status__in=(FlowRun.STATUS_ACTIVE, FlowRun.STATUS_WAITING)
    )

    contact_ids = list(
        Contact.objects.filter(org=org, status=Contact.STATUS_ACTIVE, is_active=True)
        .filter(id__in=runs.values_list("contact", flat=True))
        .values_list("id", flat=True)
    )

    if contact_ids:
        broadcast = Broadcast.create(org, user, text, contact_ids=contact_ids)
        broadcast.send_async()

        analytics.track(user, "temba.broadcast_created", dict(contacts=len(contact_ids), groups=0, urns=0))


@shared_task(track_started=True, name="fail_old_messages")
def fail_old_messages():  # pragma: needs cover
    Msg.fail_old_messages()


@shared_task(track_started=True, name="export_sms_task")
def export_messages_task(export_id):
    """
    Export messages to a file and e-mail a link to the user
    """
    ExportMessagesTask.objects.select_related("org", "created_by").get(id=export_id).perform()


@nonoverlapping_task(track_started=True, name="squash_msgcounts", lock_timeout=7200)
def squash_msgcounts():
    SystemLabelCount.squash()
    LabelCount.squash()
    BroadcastMsgCount.squash()


@shared_task(track_started=True, name="get_calculated_values")
def get_calculated_values(org_id):  # pragma: no cover
    label_mapping = dict(
        text_flows=SystemLabel.TYPE_FLOWS,
        voice_flows=SystemLabel.TYPE_FLOW_VOICE,
        sent_text=SystemLabel.TYPE_SENT,
        sent_voice=SystemLabel.TYPE_SENT_VOICE,
    )

    results = Msg.objects.filter(org=org_id).aggregate(
        text_flows=Count(
            Case(
                When(direction=Msg.DIRECTION_IN, visibility=Msg.VISIBILITY_VISIBLE, msg_type=Msg.TYPE_FLOW, then=1),
                output_field=IntegerField(),
            )
        ),
        voice_flows=Count(
            Case(
                When(direction=Msg.DIRECTION_IN, visibility=Msg.VISIBILITY_VISIBLE, msg_type=Msg.TYPE_IVR, then=1),
                output_field=IntegerField(),
            )
        ),
        sent_voice=Count(
            Case(
                When(
                    direction=Msg.DIRECTION_OUT,
                    visibility=Msg.VISIBILITY_VISIBLE,
                    status__in=(Msg.STATUS_WIRED, Msg.STATUS_SENT, Msg.STATUS_DELIVERED),
                    msg_type=Msg.TYPE_IVR,
                    then=1,
                ),
                output_field=IntegerField(),
            )
        ),
        sent_text=Count(
            Case(
                When(
                    Q(direction=Msg.DIRECTION_OUT)
                    & Q(visibility=Msg.VISIBILITY_VISIBLE)
                    & Q(status__in=(Msg.STATUS_WIRED, Msg.STATUS_SENT, Msg.STATUS_DELIVERED))
                    & ~Q(msg_type=Msg.TYPE_IVR),
                    then=1,
                ),
                output_field=IntegerField(),
            )
        ),
    )

    for key, count in results.items():
        label_type = label_mapping[key]
        print(f"Recalculating for Org {org_id}, label type {label_type}, message count {count}.")
        update_system_label_counts(count, org_id, label_type)


def update_system_label_counts(count, org_id, label_type):  # pragma: no cover
    try:
        obj = SystemLabelCount.objects.get(org_id=org_id, label_type=label_type)
        obj.count = count
        obj.save()
    except SystemLabelCount.DoesNotExist:
        if count > 0:
            obj = SystemLabelCount(org_id=org_id, label_type=label_type, count=count)
            obj.save()


@shared_task(track_started=True, name="backfill_msg_flow")
def backfill_msg_flow(org_id):
    flow_run_ids = FlowRun.objects.filter(org__pk=org_id).exclude(events__isnull=True).values_list("id", flat=True)
    logger.warning(f"Process 'backfill_msg_flow' started for {len(flow_run_ids)} runs; Org ID: {org_id}")

    num_updated = 0

    for run_ids in chunk_list(list(flow_run_ids), 1000):
        flow_runs = FlowRun.objects.filter(id__in=run_ids).order_by("created_on")

        for run in flow_runs:
            logger.warning(f"Running for run ID {run.id}")

            msgs_uuids = [
                item.get("msg", {}).get("uuid")
                for item in run.events
                if item.get("type") in ["msg_created", "msg_received"]
            ]
            Msg.objects.filter(uuid__in=msgs_uuids).update(flow=run.flow)

        num_updated += len(run_ids)
        logger.warning(f"Updated {num_updated}/{len(flow_run_ids)}")

    logger.warning(f"Process 'backfill_msg_flow' finished for org ID: {org_id}")


@shared_task(track_started=True, name="send_unread_msgs_notification_email")
def send_unread_msgs_notification_email():
    r = get_redis_connection()
    conversations = ConversationOwner.objects.filter(conversation__status=Conversation.ACTIVE, last_read__isnull=False)
    for conversation in conversations:
        count = (
            Msg.objects.filter(
                direction=Msg.DIRECTION_IN,
                contact=conversation.conversation.contact,
                created_on__gt=conversation.last_read,
            )
            .values("contact")
            .annotate(count=Count("id"))
            .values_list("count", flat=True)[:1]
            or [0]
        )[0]
        if count and conversation.owner.email:
            email_notification_key = Conversation.EMAIL_NOTIFICATION_KEY % conversation.owner.pk
            already_sent_datetime = r.get(email_notification_key)
            already_sent_datetime = already_sent_datetime.decode("utf-8") if already_sent_datetime else ""
            yesterday = timezone.now() - timedelta(days=1)
            if already_sent_datetime and parse_datetime(already_sent_datetime) > yesterday:
                # skip when email already sent today
                continue

            query = urlencode({"contactUUID": conversation.conversation.contact.uuid})
            context = {
                "username": conversation.owner.username,
                "missing_count": count,
                "site_url": f"https://{settings.HOSTNAME}{reverse('msgs.conversation_list')}?{query}",
                "now": timezone.now(),
            }
            html_content = render_to_string("msgs/email/unread_messages.html", context)
            send_mail(
                subject=_("You have unread messages"),
                message=html_content,
                html_message=html_content,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[conversation.owner.email],
            )
            r.set(email_notification_key, timezone.now().isoformat())
