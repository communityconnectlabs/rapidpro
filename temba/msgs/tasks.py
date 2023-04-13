import logging

from django.db.models import Case, Count, IntegerField, Q, When

from celery import shared_task

from temba.utils import analytics
from temba.utils.celery import nonoverlapping_task

from .models import Broadcast, BroadcastMsgCount, ExportMessagesTask, LabelCount, Msg, SystemLabel, SystemLabelCount

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
