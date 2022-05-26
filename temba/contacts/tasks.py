import io
import json
import logging
import requests
import pandas as pd
from datetime import timedelta

import iso8601
import pytz

from django.conf import settings
from django.contrib.auth.models import User
from django.utils import timezone

from celery.task import task

from temba.orgs.models import Org
from temba.utils import chunk_list
from temba.utils.celery import nonoverlapping_task
from temba.utils.email import send_email_with_attachments

from .models import Contact, ContactGroup, ContactGroupCount, ContactImport, ExportContactsTask
from .search import elastic

logger = logging.getLogger(__name__)


@task(track_started=True)
def release_contacts(user_id, contact_ids):
    """
    Releases the given contacts
    """
    user = User.objects.get(pk=user_id)

    for id_batch in chunk_list(contact_ids, 100):
        batch = Contact.objects.filter(id__in=id_batch, is_active=True).prefetch_related("urns")
        for contact in batch:
            contact.release(user)


@task(track_started=True)
def import_contacts_task(import_id):
    """
    Import contacts from a spreadsheet
    """
    ContactImport.objects.get(id=import_id).start()


@task(track_started=True, name="export_contacts_task")
def export_contacts_task(task_id):
    """
    Export contacts to a file and e-mail a link to the user
    """
    ExportContactsTask.objects.get(id=task_id).perform()


@nonoverlapping_task(track_started=True, name="release_group_task")
def release_group_task(group_id):
    """
    Releases group
    """
    ContactGroup.all_groups.get(id=group_id).release()


@nonoverlapping_task(track_started=True, name="squash_contactgroupcounts", lock_timeout=7200)
def squash_contactgroupcounts():
    """
    Squashes our ContactGroupCounts into single rows per ContactGroup
    """
    ContactGroupCount.squash()


@task(track_started=True, name="full_release_contact")
def full_release_contact(contact_id):
    contact = Contact.objects.filter(id=contact_id).first()

    if contact and not contact.is_active:
        contact._full_release()


@task(name="check_elasticsearch_lag")
def check_elasticsearch_lag():
    if settings.ELASTICSEARCH_URL:
        es_last_modified_contact = elastic.get_last_modified()

        if es_last_modified_contact:
            # if we have elastic results, make sure they aren't more than five minutes behind
            db_contact = Contact.objects.order_by("-modified_on").first()
            es_modified_on = iso8601.parse_date(es_last_modified_contact["modified_on"], pytz.utc)
            es_id = es_last_modified_contact["id"]

            # no db contact is an error, ES should be empty as well
            if not db_contact:
                logger.error(
                    "db empty but ElasticSearch has contacts. Newest ES(id: %d, modified_on: %s)",
                    es_id,
                    es_modified_on,
                )
                return False

            #  check the lag between the two, shouldn't be more than 5 minutes
            if db_contact.modified_on - es_modified_on > timedelta(minutes=5):
                logger.error(
                    "drift between ElasticSearch and DB. Newest DB(id: %d, modified_on: %s) Newest ES(id: %d, modified_on: %s)",
                    db_contact.id,
                    db_contact.modified_on,
                    es_id,
                    es_modified_on,
                )

                return False

        else:
            # we don't have any ES hits, get our oldest db contact, check it is less than five minutes old
            db_contact = Contact.objects.order_by("modified_on").first()
            if db_contact and timezone.now() - db_contact.modified_on > timedelta(minutes=5):
                logger.error(
                    "ElasticSearch empty with DB contacts older than five minutes. Oldest DB(id: %d, modified_on: %s)",
                    db_contact.id,
                    db_contact.modified_on,
                )

                return False

    return True


@nonoverlapping_task(track_started=True, name="block_deactivated_contacts_task")
def block_deactivated_contacts_task():
    email_subject = f"{timezone.now().strftime('%B %d, %Y')} - list of deactivated phone numbers."
    email_text = """
    Hi There!
    We have prepared a list of contacts that were blocked because of deactivated phone number.
    You can download it in the attached file.

    Thanks,
    The CommunityConnectLabs Team.
    """

    all_blocked_contacts = {}
    numbers, formatted_numbers = None, "('')"
    for org in Org.objects.filter(is_active=True):
        client = org.get_twilio_client()
        if not client:
            continue

        try:
            if numbers is None:
                # get the link from twilio to download deactivated phone numbers
                yesterday = (timezone.now() - timedelta(days=1)).strftime("%Y-%m-%d")
                response = client.request("GET", "https://messaging.twilio.com/v1/Deactivations", {"Date": yesterday})
                response_json = json.loads(response.text)
                if not response.ok or not response_json.get("redirect_to"):
                    continue

                # download and parse deactivated phone numbers
                redirect_to = response_json.get("redirect_to")
                response = requests.get(redirect_to)
                if not response.ok:
                    continue
                numbers = response.text.split("\n")
                formatted_numbers = ", ".join(map(lambda x: f"('{x}')", numbers)) or "('')"

            # get contacts from the db that have phone number deactivated and block them
            contacts_to_block = list(
                Contact.objects.raw(
                    f"""
                SELECT contacts_contact.*, contacts_contacturn.path as phone_number FROM contacts_contact
                INNER JOIN contacts_contacturn ON contacts_contact.id = contacts_contacturn.contact_id
                INNER JOIN (VALUES {formatted_numbers}) AS ci(number)
                ON replace(contacts_contacturn.path, '+', '') = replace(ci.number, '+', '')
                WHERE contacts_contacturn.scheme = 'tel' AND contacts_contact.status = 'A'
                AND contacts_contact.org_id = {org.id};
                """
                )
            )
            Contact.apply_action_block(org.get_admins().first(), contacts_to_block)

            # send emails to admins if there any blocked contact
            if contacts_to_block:
                admin_emails = [admin.email for admin in org.get_admins().order_by("email")]
                if len(admin_emails) == 0:
                    return

                memory_file = io.StringIO()
                org_phone_numbers = [contact.phone_number for contact in contacts_to_block]
                all_blocked_contacts[org.name] = org_phone_numbers
                phone_numbers_df = pd.DataFrame({"Disconnected Phone Numbers": org_phone_numbers})
                phone_numbers_df.to_csv(memory_file, index=False)
                memory_file.seek(0)
                send_email_with_attachments(
                    subject=email_subject,
                    text=email_text,
                    recipient_list=admin_emails,
                    attachments=[
                        (
                            f"deactivated_phone_numbers_{timezone.now().strftime('%Y_%m_%d')}.csv",
                            memory_file.read(),
                            "text/csv",
                        )
                    ],
                )
        except json.JSONDecodeError:
            continue

    if all_blocked_contacts:
        memory_file = io.StringIO()
        phone_numbers_df = pd.DataFrame(all_blocked_contacts)
        phone_numbers_df.to_csv(memory_file, index=False)
        memory_file.seek(0)
        send_email_with_attachments(
            subject=email_subject,
            text=email_text,
            recipient_list=["josh@communityconnectlabs.com"],
            attachments=[
                (
                    f"deactivated_phone_numbers_{timezone.now().strftime('%Y_%m_%d')}.csv",
                    memory_file.read(),
                    "text/csv",
                )
            ],
        )
