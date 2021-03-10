import re
import time

from itertools import chain

from django.conf import settings
from django.db import models
from django.db.models.functions import Length
from django.utils.translation import ugettext_lazy as _
from django.urls import reverse

from jellyfish import jaro_distance
from smartmin.models import SmartModel

from temba.assets.models import register_asset_store
from temba.contacts.models import Contact
from temba.contacts.search import SearchException
from temba.orgs.models import Org
from temba.utils import chunk_list
from temba.utils.dates import datetime_to_str
from temba.utils.models import TembaModel, URLTextField
from temba.utils.export import BaseExportAssetStore, BaseExportTask, TableExporter
from temba.utils.text import clean_string


MAX_HISTORY = 50


class LinkException(Exception):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class Link(TembaModel):

    name = models.CharField(max_length=64, help_text=_("The name for this trackable link"))

    destination = URLTextField(help_text="The destination URL for this trackable link")

    org = models.ForeignKey(Org, related_name="links", on_delete=models.CASCADE)

    related_flow = models.ForeignKey(
        "flows.Flow",
        related_name="related_links",
        on_delete=models.CASCADE,
        null=True,
        default=None,
        help_text=_("Make this link related to a specific flow. (leave empty to make related to all flows)"),
    )

    is_archived = models.BooleanField(default=False, help_text=_("Whether this trackable link is archived"))

    clicks_count = models.PositiveIntegerField(default=0, help_text="Clicks count for this trackable link")

    @classmethod
    def create(cls, org, user, name, destination, related_flow=None):
        links_arg = dict(
            org=org, name=name, destination=destination, related_flow=related_flow, created_by=user, modified_by=user
        )
        return Link.objects.create(**links_arg)

    def as_select2(self):
        return dict(text=self.name, id=self.uuid)

    def as_json(self):
        return dict(uuid=self.uuid, name=self.name, destination=self.destination)

    def get_permalink(self):
        return reverse("links.link_handler", args=[self.uuid])

    def get_url(self):
        protocol = "http" if settings.DEBUG else "https"
        return f"{protocol}://{settings.HOSTNAME}{self.get_permalink()}"

    @classmethod
    def apply_action_archive(cls, user, links):
        changed = []

        for link in links:
            link.archive()
            changed.append(link.pk)

        return changed

    @classmethod
    def apply_action_restore(cls, user, links):
        changed = []
        for link in links:
            try:
                link.restore()
                changed.append(link.pk)
            except LinkException:  # pragma: no cover
                pass
        return changed

    def archive(self):
        self.is_archived = True
        self.save(update_fields=["is_archived"])

    def restore(self):
        self.is_archived = False
        self.save(update_fields=["is_archived"])

    def get_activity(self, after, before, search):
        """
        Gets this link's activity of contacts in the given time window
        """

        contacts = LinkContacts.objects.filter(link=self, created_on__gte=after, created_on__lt=before)
        if search:
            try:
                contacts = Contact.objects.filter(
                    models.Q(name__icontains=search) | models.Q(urns__path__icontains=search),
                    id__in=contacts.values_list("contact__id"),
                ).only("id")
            except SearchException as e:
                self.search_error = str(e.message)
                contacts = Contact.objects.none()

        # wrap items, chain and sort by time
        activity = chain([{"type": "contact", "time": c.created_on, "obj": c} for c in contacts])

        return sorted(activity, key=lambda i: i["time"], reverse=True)[:MAX_HISTORY]

    @classmethod
    def import_links(cls, org, user, link_defs):
        """
        Import links from a list of exported links
        """

        for link_def in link_defs:
            name = link_def.get("name")
            destination = link_def.get("destination")
            uuid = link_def.get("uuid")

            link = Link.objects.filter(uuid=uuid, org=org).first()

            # first check if we have the objects by UUID
            if link:
                link.name = name
                link.destination = destination
                link.save(update_fields=["name", "destination"])
            else:
                dict_args = dict(
                    name=name, destination=destination, org=org, uuid=uuid, created_by=user, modified_by=user
                )
                Link.objects.create(**dict_args)

    @classmethod
    def check_misstyped_links(cls, flow, definition):
        links = cls.objects.filter(org=flow.org, is_archived=False).order_by(Length("destination").asc())
        issues = []
        action_list = []
        pattern = r"\b(?P<url>(?:http(s)?:\/\/)?[\w.-]+(?:\.[\w\.-]+)+[\w\-\._~:/?#[\]@!\$&'\(\)\*\+,;=.]+)(\s|$)"
        for node in definition.get("nodes", []):
            for action in node.get("actions", []):
                if action["type"] == "send_msg":
                    for match in re.finditer(pattern, action["text"], re.IGNORECASE):
                        action_list.append(
                            {
                                "node_uuid": node["uuid"],
                                "action_uuid": action["uuid"],
                                "url": match.groupdict().get("url"),
                            }
                        )

        for action in action_list:
            if cls.objects.filter(org=flow.org, is_archived=False, destination=action["url"]).exists():
                continue

            for link in links:
                if jaro_distance(action["url"], link.destination) >= 0.9 and action["url"] != link.destination:
                    issues.append(
                        {
                            "type": "invalid_link",
                            "node_uuid": action["node_uuid"],
                            "action_uuid": action["action_uuid"],
                            "actual_link": action["url"],
                            "expected_link": link.destination,
                        }
                    )
                    break
        return issues

    def update_flows(self):
        from temba.flows.models import FlowRevision

        revisions = []
        raw_query = FlowRevision.objects.raw(
            "SELECT * FROM flows_flowrevision "
            "LEFT JOIN (SELECT flow_id, MAX(revision) as latest_revision FROM flows_flowrevision GROUP BY flow_id) as latest_revisions "
            "ON flows_flowrevision.flow_id=latest_revisions.flow_id "
            "WHERE flows_flowrevision.revision=latest_revisions.latest_revision AND "
            "flows_flowrevision.definition::text LIKE %s;",
            [f"%{self.uuid}%"],
        )

        def update_node(node):
            updated = False
            if str(self.uuid) not in str(node):
                return False
            for action in node.get("actions", []):
                if action.get("type") == "call_shorten_url" and action.get("shorten_url", {}).get("id") == str(
                    self.uuid
                ):
                    action["shorten_url"]["text"] = self.name
                    updated = True
            return updated

        for revision in raw_query:
            if any(list(map(update_node, revision.definition.get("nodes", [])))):
                revisions.append(revision)
        FlowRevision.objects.bulk_update(revisions, ["definition"])

    def __str__(self):
        return self.name

    class Meta:
        ordering = ("-created_on",)


class LinkContacts(SmartModel):
    link = models.ForeignKey(Link, related_name="contacts", on_delete=models.CASCADE)

    contact = models.ForeignKey(
        Contact,
        related_name="contact_links",
        help_text=_("The users which clicked on this link"),
        on_delete=models.CASCADE,
    )

    def __str__(self):
        return f"{self.contact.get_display()}"


class ExportLinksTask(BaseExportTask):
    analytics_key = "link_export"
    email_subject = "Your trackable link export from %s is ready"
    email_template = "links/email/links_export_download"

    link = models.ForeignKey(
        Link, null=True, related_name="exports", help_text=_("The trackable link to export"), on_delete=models.CASCADE
    )

    @classmethod
    def create(cls, org, user, link):
        return cls.objects.create(org=org, link=link, created_by=user, modified_by=user)

    def get_export_fields_and_schemes(self):

        fields = [
            dict(label="Contact UUID", key=Contact.UUID, id=0, field=None, urn_scheme=None),
            dict(label="Name", key=Contact.NAME, id=0, field=None, urn_scheme=None),
            dict(label="Date", key="date", id=0, field=None, urn_scheme=None),
            dict(label="Destination Link", key="destination", id=0, field=None, urn_scheme=None),
        ]

        # anon orgs also get an ID column that is just the PK
        if self.org.is_anon:
            fields = [dict(label="ID", key=Contact.ID, id=0, field=None, urn_scheme=None)] + fields

        return fields, dict()

    def write_export(self):
        fields, scheme_counts = self.get_export_fields_and_schemes()

        contact_ids = self.link.contacts.all().order_by("contact__name", "contact__id").values_list("id", flat=True)

        # create our exporter
        exporter = TableExporter(self, "Links", [f["label"] for f in fields])

        current_contact = 0
        start = time.time()

        # write out contacts in batches to limit memory usage
        for batch_ids in chunk_list(contact_ids, 1000):
            # fetch all the contacts for our batch
            batch_contacts = LinkContacts.objects.filter(id__in=batch_ids)

            # to maintain our sort, we need to lookup by id, create a map of our id->contact to aid in that
            contact_by_id = {c.id: c for c in batch_contacts}

            for contact_id in batch_ids:
                contact = contact_by_id[contact_id]

                values = []
                for col in range(len(fields)):
                    field = fields[col]

                    if field["key"] == Contact.ID:
                        field_value = str(contact.contact.id)
                    elif field["key"] == Contact.NAME:
                        field_value = contact.contact.get_display()
                    elif field["key"] == Contact.UUID:
                        field_value = contact.contact.uuid
                    elif field["key"] == "date":
                        field_value = datetime_to_str(
                            contact.created_on, format="%m-%d-%Y %H:%M:%S", tz=self.link.org.timezone
                        )
                    elif field["key"] == "destination":
                        field_value = contact.link.destination
                    elif field["field"] is not None:
                        field_value = contact.contact.get_field_display(field["field"])
                    else:
                        field_value = ""

                    if field_value is None:
                        field_value = ""

                    if field_value:
                        field_value = clean_string(field_value)

                    values.append(field_value)

                # write this contact's values
                exporter.write_row(values)
                current_contact += 1

                # output some status information every 10,000 contacts
                if current_contact % 10000 == 0:  # pragma: no cover
                    elapsed = time.time() - start
                    predicted = int(elapsed / (current_contact / (len(contact_ids) * 1.0)))

                    print(
                        "Export of %s contacts - %d%% (%s/%s) complete in %0.2fs (predicted %0.0fs)"
                        % (
                            self.org.name,
                            current_contact * 100 / len(contact_ids),
                            "{:,}".format(current_contact),
                            "{:,}".format(len(contact_ids)),
                            time.time() - start,
                            predicted,
                        )
                    )

        return exporter.save_file()


@register_asset_store
class ContactExportAssetStore(BaseExportAssetStore):
    model = ExportLinksTask
    key = "link_export"
    directory = "link_exports"
    permission = "links.link_export"
    extensions = ("xlsx", "csv")
