import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import iso8601
import regex
import requests
from django_redis import get_redis_connection
from packaging.version import Version
from simplejson import JSONDecodeError
from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartFormView,
    SmartListView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
    smart_url,
)

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import FileExtensionValidator
from django.db.models import Count, Max, Min, Sum
from django.db.models.functions import Lower
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.encoding import force_str
from django.utils.functional import cached_property
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView

from temba import mailroom
from temba.archives.models import Archive
from temba.channels.models import Channel
from temba.classifiers.models import Classifier
from temba.classifiers.types.dialogflow.type import DialogflowType
from temba.contacts.models import URN, ContactField, ContactGroup
from temba.contacts.search import SearchException, parse_query
from temba.contacts.search.elastic import query_contact_ids
from temba.contacts.search.omnibox import omnibox_deserialize
from temba.flows.models import (
    Flow,
    FlowRevision,
    FlowRun,
    FlowRunCount,
    FlowSession,
    FlowStart,
    FlowTemplate,
    FlowTemplateGroup,
    StudioFlowStart,
)
from temba.flows.tasks import download_flow_images_task, export_flow_results_task, update_session_wait_expires
from temba.ivr.models import IVRCall
from temba.links.models import Link, LinkContacts
from temba.mailroom import FlowValidationException
from temba.msgs.models import Attachment
from temba.orgs.models import GIFTCARDS, LOOKUPS, IntegrationType, Org
from temba.orgs.views import MenuMixin, ModalMixin, OrgFilterMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.triggers.models import Trigger
from temba.utils import analytics, build_flow_parameters, gettext, json, languages, on_transaction_commit, str_to_bool
from temba.utils.fields import (
    CheckboxWidget,
    CompletionTextarea,
    ContactSearchWidget,
    InputWidget,
    OmniboxChoice,
    OmniboxField,
    SelectMultipleWidget,
    SelectWidget,
)
from temba.utils.s3 import private_file_storage
from temba.utils.text import slugify_with
from temba.utils.uuid import uuid4
from temba.utils.views import BulkActionMixin, SpaMixin

from ..templates.models import Template
from . import legacy
from .merging import (
    Graph,
    GraphDifferenceMap,
    deserialize_dict_param_from_request,
    deserialize_difference_graph,
    serialize_difference_graph,
)
from .models import (
    ExportFlowImagesTask,
    ExportFlowResultsTask,
    FlowImage,
    FlowLabel,
    FlowStartCount,
    FlowUserConflictException,
    FlowVersionConflictException,
    MergeFlowsTask,
)

logger = logging.getLogger(__name__)

EXPIRES_CHOICES = (
    (5, _("After 5 minutes")),
    (10, _("After 10 minutes")),
    (15, _("After 15 minutes")),
    (30, _("After 30 minutes")),
    (60, _("After 1 hour")),
    (60 * 3, _("After 3 hours")),
    (60 * 6, _("After 6 hours")),
    (60 * 12, _("After 12 hours")),
    (60 * 18, _("After 18 hours")),
    (60 * 24, _("After 1 day")),
    (60 * 24 * 2, _("After 2 days")),
    (60 * 24 * 3, _("After 3 days")),
    (60 * 24 * 7, _("After 1 week")),
    (60 * 24 * 14, _("After 2 weeks")),
    (60 * 24 * 30, _("After 30 days")),
)

LAUNCH_IMMEDIATELY = "LAUNCH_IMMEDIATELY"
LAUNCH_ON_KEYWORD_TRIGGER = "LAUNCH_ON_KEYWORD_TRIGGER"
LAUNCH_ON_SHEDULE_TRIGGER = "LAUNCH_ON_SHEDULE_TRIGGER"
LAUNCH_CHOICES = (
    (LAUNCH_IMMEDIATELY, _("Start flow immediately and send to selected groups or individual")),
    (LAUNCH_ON_KEYWORD_TRIGGER, _("Start flow when a specified keyword is received in a message")),
    (LAUNCH_ON_SHEDULE_TRIGGER, _("Start flow at a future date and time")),
)


class BaseFlowForm(forms.ModelForm):
    def clean_keyword_triggers(self):
        org = self.user.get_org()
        value = self.data.getlist("keyword_triggers", [])

        duplicates = []
        wrong_format = []
        cleaned_keywords = []

        for keyword in value:
            keyword = keyword.lower().strip()
            if not keyword:  # pragma: needs cover
                continue

            if (
                not regex.match(r"^\w+$", keyword, flags=regex.UNICODE | regex.V0)
                or len(keyword) > Trigger.KEYWORD_MAX_LEN
            ):
                wrong_format.append(keyword)

            # make sure it won't conflict with existing triggers
            conflicts = Trigger.get_conflicts(org, Trigger.TYPE_KEYWORD, keyword=keyword)
            if self.instance:
                conflicts = conflicts.exclude(flow=self.instance.id)

            if conflicts:
                duplicates.append(keyword)
            else:
                cleaned_keywords.append(keyword)

        if wrong_format:
            raise forms.ValidationError(
                _(
                    '"%(keyword)s" must be a single word, less than %(limit)d characters, containing only letter '
                    "and numbers"
                )
                % dict(keyword=", ".join(wrong_format), limit=Trigger.KEYWORD_MAX_LEN)
            )

        if duplicates:
            if len(duplicates) > 1:
                error_message = _('The keywords "%s" are already used for another flow') % ", ".join(duplicates)
            else:
                error_message = _('The keyword "%s" is already used for another flow') % ", ".join(duplicates)
            raise forms.ValidationError(error_message)

        return ",".join(cleaned_keywords)

    class Meta:
        model = Flow
        fields = "__all__"


class PartialTemplate(SmartTemplateView):  # pragma: no cover
    def pre_process(self, request, *args, **kwargs):
        self.template = kwargs["template"]
        return

    def get_template_names(self):
        return "partials/%s.html" % self.template


class FlowSessionCRUDL(SmartCRUDL):
    actions = ("json",)
    model = FlowSession

    class Json(SmartReadView):
        slug_url_kwarg = "uuid"
        permission = "flows.flowsession_json"

        def get(self, request, *args, **kwargs):
            session = self.get_object()
            output = session.output_json
            output["_metadata"] = dict(
                session_id=session.id, org=session.org.name, org_id=session.org_id, site=self.request.branding["link"]
            )
            return JsonResponse(output, json_dumps_params=dict(indent=2))


class FlowRunCRUDL(SmartCRUDL):
    actions = ("delete",)
    model = FlowRun

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ("pk",)
        success_message = None

        def post(self, request, *args, **kwargs):
            flow_run = self.get_object()
            flow = flow_run.flow
            flow_run.delete()

            # mark flow as updated to be able to refresh analytics
            flow.modified_on = timezone.now()
            flow.save(update_fields=["modified_on"])
            return HttpResponse()


class FlowImageCRUDL(SmartCRUDL):
    actions = ("list", "read", "filter", "archived", "download")
    model = FlowImage

    class OrgQuerysetMixin(object):
        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            if not self.request.user.is_authenticated:  # pragma: needs cover
                return queryset.exclude(pk__gt=0)
            else:
                return queryset.filter(org=self.request.user.get_org())

    class BaseList(BulkActionMixin, OrgQuerysetMixin, OrgPermsMixin, SmartListView):
        title = _("Flow Images")
        refresh = 86_400_000  # set to 1 day to be able to refresh a page only when a change has been made
        fields = ("name", "modified_on")
        default_template = "flowimages/flowimage_list.html"
        default_order = ("-created_on",)
        search_fields = ("name__icontains", "contact__name__icontains", "contact__urns__path__icontains")
        bulk_actions = ("archive", "delete", "download", "restore")

        def get_counter(self):
            query = FlowImage.objects.filter(org=self.request.user.get_org())
            return query.filter(is_active=True).count(), query.filter(is_active=False).count()

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            folders = self.get_folders()
            context["org_has_flowimages"] = folders[0].get("count")
            context["org_has_flowimages_archived"] = folders[1].get("count")
            context["flows"] = (
                Flow.objects.filter(org=self.request.user.get_org(), is_active=True)
                .exclude(is_system=True)
                .only("name", "uuid")
                .order_by("name")
            )
            context["groups"] = (
                ContactGroup.user_groups.filter(org=self.request.user.get_org(), is_active=True)
                .only("name", "uuid")
                .order_by("name")
            )
            context["folders"] = folders
            context["request_url"] = self.request.path
            context["actions"] = self.actions
            context["contact_fields"] = ContactField.user_fields.filter(
                org=self.request.user.get_org(), is_active=True
            ).order_by("pk")
            return context

        def get_folders(self):
            (active_count, archived_count) = self.get_counter()
            return [
                dict(label="Active", url=reverse("flows.flowimage_list"), count=active_count),
                dict(label="Archived", url=reverse("flows.flowimage_archived"), count=archived_count),
            ]

        def derive_queryset(self, *args, **kwargs):
            return super().derive_queryset(*args, **kwargs)

        def get_gear_links(self):
            links = []
            if self.has_org_perm("flows.flowimage_download") and self.object_list:
                links.append(dict(title=_("Download all images"), style="download-all-images", href="#"))
            return links

    class List(BaseList):
        title = _("Flow Images")
        actions = ("download", "archive")

        def derive_queryset(self, *args, **kwargs):
            qs = super().derive_queryset(*args, **kwargs)
            qs = qs.distinct()
            return qs.exclude(is_active=False)

    class Archived(BaseList):
        title = _("Flow Images Archived")
        actions = ("download", "restore", "delete")

        def derive_queryset(self, *args, **kwargs):
            qs = super().derive_queryset(*args, **kwargs)
            qs = qs.distinct()
            return qs.exclude(is_active=True)

    class Filter(BaseList):
        actions = ("download", "archive")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["current_object"] = self.derive_object()
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<type>group|flow)/(?P<uuid>[^/]+)/$" % (path, action)

        def derive_title(self, *args, **kwargs):
            obj = self.derive_object()
            return _('Images from "%s"' % obj.name) if obj else _("Flow Images")

        def derive_object(self):
            obj_type = self.kwargs.get("type")
            uuid = self.kwargs.get("uuid")
            if obj_type == "flow":
                return get_object_or_404(Flow, org=self.org, uuid=uuid)
            else:
                return get_object_or_404(ContactGroup.user_groups, org=self.org, uuid=uuid)

        def get_queryset_filter(self):
            obj = self.derive_object()
            obj_type = self.kwargs.get("type")
            if obj_type == "flow":
                get_filter = dict(flow=obj)
            else:
                contacts_id = (
                    obj.contacts.all().exclude(is_active=False).order_by("pk").values_list("pk", flat=True).distinct()
                )
                get_filter = dict(contact__id__in=contacts_id)
            return get_filter

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            _filter = self.get_queryset_filter()
            qs = qs.filter(**_filter).exclude(is_active=False).distinct()
            return qs

        def derive_queryset(self, *args, **kwargs):
            qs = super().derive_queryset(*args, **kwargs)
            qs = qs.distinct()
            return qs

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get(self, request, *args, **kwargs):
            flow_image = self.get_object()
            with open(flow_image.get_full_path(), "r") as image:
                return HttpResponse(image.read(), content_type="image/png")

    class Download(OrgPermsMixin, SmartListView):
        def post(self, request, *args, **kwargs):
            user = self.request.user
            org = user.get_org()

            type_ = self.request.POST.get("type", None)
            uuid_ = self.request.POST.get("uuid", None)

            if type_ in ["list", "archived", "group", "flow"]:
                if type_ == "list":
                    get_filter = dict(is_active=True, org=org)
                elif type_ == "archived":
                    get_filter = dict(is_active=False, org=org)
                elif (type_ == "group" or type_ == "flow") and uuid_:
                    if type_ == "group":
                        group = ContactGroup.user_groups.filter(org=org, uuid=uuid_).only("id").first()
                        contacts_id = (
                            group.contacts.all()
                            .exclude(is_active=False)
                            .order_by("id")
                            .values_list("pk", flat=True)
                            .distinct()
                        )
                        get_filter = dict(is_active=True, contact__id__in=contacts_id, org=org)
                    else:
                        flow = Flow.objects.filter(org=org, uuid=uuid_).first()
                        get_filter = dict(is_active=True, flow=flow, org=org)
                else:
                    get_filter = None

                if get_filter:
                    objects_list = list(
                        FlowImage.objects.filter(**get_filter)
                        .only("id")
                        .order_by("-created_on")
                        .values_list("id", flat=True)
                        .distinct()
                    )
                else:
                    objects_list = []
            else:
                objects = self.request.POST.get("objects")
                objects_list = objects.split(",")

            # is there already an export taking place?
            existing = ExportFlowImagesTask.get_recent_unfinished(org)
            if existing:
                messages.info(
                    self.request,
                    _(
                        "There is already a download in progress, started by %s. You must wait "
                        "for that download process to complete before starting another." % existing.created_by.username
                    ),
                )
            else:
                export = ExportFlowImagesTask.create(org, user, files=objects_list)
                on_transaction_commit(lambda: download_flow_images_task.delay(export.pk))

                if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):  # pragma: needs cover
                    messages.info(
                        self.request,
                        _("We are preparing your download file. We will e-mail you at %s when it is ready.")
                        % self.request.user.username,
                    )
                else:
                    export = ExportFlowImagesTask.objects.get(id=export.pk)
                    dl_url = reverse("assets.download", kwargs=dict(type="flowimages_download", pk=export.pk))
                    messages.info(
                        self.request,
                        _("Download complete, you can find it here: %s (production users will get an email)") % dl_url,
                    )

            return HttpResponseRedirect(request.META.get("HTTP_REFERER"))


class FlowCRUDL(SmartCRUDL):
    actions = (
        "list",
        "archived",
        "copy",
        "create",
        "delete",
        "update",
        "menu",
        "simulate",
        "change_language",
        "export_translation",
        "download_translation",
        "import_translation",
        "export_results",
        "upload_action_recording",
        "editor",
        "results",
        "run_table",
        "links_table",
        "category_counts",
        "broadcast",
        "activity",
        "activity_chart",
        "filter",
        "campaign",
        "revisions",
        "recent_contacts",
        "assets",
        "upload_media_action",
        # "pdf_export",
        "lookups_api",
        "giftcards_api",
        "launch",
        "flow_parameters",
        "export_pdf",
        "merge_flows",
        "merging_flows_table",
        "launch_studio_flow",
        "dialogflow_api",
        "show_templates",
        "monitoring",
    )

    model = Flow

    class LookupsApi(OrgPermsMixin, SmartListView):
        def get(self, request, *args, **kwargs):
            db = self.request.GET.get("db", None)
            collections = []

            if db:
                headers = {
                    "X-Parse-Application-Id": settings.PARSE_APP_ID,
                    "X-Parse-Master-Key": settings.PARSE_MASTER_KEY,
                    "Content-Type": "application/json",
                }
                url = f"{settings.PARSE_URL}/schemas/{db}"
                response = requests.get(url, headers=headers)
                response_json = response.json()
                if response.status_code == 200 and "fields" in response_json:
                    fields = response_json["fields"]
                    for key in sorted(fields.keys()):
                        default_fields = ["ACL", "createdAt", "updatedAt", "order"]
                        if key not in default_fields:
                            field_type = fields[key]["type"] if "type" in fields[key] else None
                            collections.append(dict(id=key, text=key, type=field_type))
            else:
                org = self.request.user.get_org()
                for collection in org.get_collections(collection_type=LOOKUPS):
                    slug_collection = slugify(collection)
                    collection_full_name = (
                        f"{settings.PARSE_SERVER_NAME}_{org.slug}_{org.id}_{str(LOOKUPS).lower()}_{slug_collection}"
                    )
                    collection_full_name = collection_full_name.replace("-", "")
                    collections.append(dict(id=collection_full_name, text=collection))
            return JsonResponse(dict(results=collections))

    class GiftcardsApi(OrgPermsMixin, SmartListView):
        def get(self, request, *args, **kwargs):
            collections = []
            org = self.request.user.get_org()
            for collection in org.get_collections(collection_type=GIFTCARDS):
                slug_collection = slugify(collection)
                collection_full_name = (
                    f"{settings.PARSE_SERVER_NAME}_{org.slug}_{org.id}_{str(GIFTCARDS).lower()}_{slug_collection}"
                )
                collection_full_name = collection_full_name.replace("-", "")
                collections.append(dict(id=collection_full_name, text=collection))
            return JsonResponse(dict(results=collections))

    class DialogflowApi(OrgPermsMixin, SmartListView):
        def get(self, request, *args, **kwargs):
            org = self.request.user.get_org()

            response = self.get_projects(org)
            return JsonResponse(dict(results=response))

        @classmethod
        def get_projects(cls, org):
            items = Classifier.objects.filter(classifier_type=DialogflowType.slug, org=org, is_active=True)
            projects = []
            for item in items:
                projects.append(dict(id=item.uuid, text=item.name))
            return projects

    class FlowParameters(OrgPermsMixin, SmartListView):
        def get(self, request, *args, **kwargs):
            flow_id = self.request.GET.get("flow_id", None)
            org = self.request.user.get_org()
            flow = Flow.objects.filter(is_active=True, org=org, id=int(flow_id)).first()
            params = sorted(flow.get_trigger_params()) if flow else []
            return JsonResponse(dict(results=params))

    class AllowOnlyActiveFlowMixin:
        def get_queryset(self):
            initial_queryset = super().get_queryset()
            return initial_queryset.filter(is_active=True)

    class Menu(MenuMixin, SmartTemplateView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/((?P<submenu>[A-z]+)/)?$" % (path, action)

        def derive_menu(self):

            labels = FlowLabel.objects.filter(org=self.request.user.get_org(), parent=None)

            menu = []
            menu.append(self.create_menu_item(name=_("Active"), icon="flow", href="flows.flow_list"))

            for label in labels:
                menu.append(
                    self.create_menu_item(
                        icon="tag",
                        menu_id=label.uuid,
                        name=label.name,
                        href=reverse("flows.flow_filter", args=[label.uuid]),
                    )
                )

            menu.append(self.create_menu_item(name=_("Archived"), icon="archive", href="flows.flow_archived"))
            return menu

    class RecentContacts(OrgObjPermsMixin, SmartReadView):
        """
        Used by the editor for the rollover of recent contacts coming out of a split
        """

        slug_url_kwarg = "uuid"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return rf"^{path}/{action}/(?P<uuid>[0-9a-f-]+)/(?P<exit_uuid>[0-9a-f-]+)/(?P<dest_uuid>[0-9a-f-]+)/$"

        def render_to_response(self, context, **response_kwargs):
            exit_uuid, dest_uuid = self.kwargs["exit_uuid"], self.kwargs["dest_uuid"]

            return JsonResponse(self.object.get_recent_contacts(exit_uuid, dest_uuid), safe=False)

    class Revisions(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        """
        Used by the editor for fetching and saving flow definitions
        """

        slug_url_kwarg = "uuid"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<uuid>[0-9a-f-]+)/((?P<revision_id>\d+)/)?$" % (path, action)

        def get(self, request, *args, **kwargs):
            flow = self.get_object()
            revision_id = self.kwargs.get("revision_id")

            # the editor requests the spec version it supports which allows us to add support for new versions
            # on the goflow/mailroom side before updating the editor to use that new version
            requested_version = request.GET.get("version", Flow.CURRENT_SPEC_VERSION)

            # we are looking for a specific revision, fetch it and migrate it forward
            if revision_id:
                revision = FlowRevision.objects.get(flow=flow, id=revision_id)
                definition = revision.get_migrated_definition(to_version=requested_version)

                # get our metadata
                flow_info = mailroom.get_client().flow_inspect(flow.org_id, definition)
                return JsonResponse(
                    {
                        "definition": definition,
                        "issues": [
                            *flow_info[Flow.INSPECT_ISSUES],
                            *Link.check_misstyped_links(flow, definition),
                            *Trigger.check_used_trigger_words(flow, definition),
                            *Attachment.validate_fields(flow.org, definition),
                        ],
                        "metadata": Flow.get_metadata(flow_info),
                    }
                )

            # build a list of valid revisions to display
            revisions = []

            for revision in flow.revisions.all().order_by("-revision")[:100]:
                revision_version = Version(revision.spec_version)

                # our goflow revisions are already validated
                if revision_version >= Version(Flow.INITIAL_GOFLOW_VERSION):
                    revisions.append(revision.as_json())
                    continue

                # legacy revisions should be validated first as a failsafe
                try:
                    legacy_flow_def = revision.get_migrated_definition(to_version=Flow.FINAL_LEGACY_VERSION)
                    FlowRevision.validate_legacy_definition(legacy_flow_def)
                    revisions.append(revision.as_json())

                except ValueError:
                    # "expected" error in the def, silently cull it
                    pass

                except Exception as e:
                    # something else, we still cull, but report it to sentry
                    logger.error(
                        f"Error validating flow revision ({flow.uuid} [{revision.id}]): {str(e)}", exc_info=True
                    )
                    pass

            return JsonResponse({"results": revisions}, safe=False)

        def post(self, request, *args, **kwargs):
            if not self.has_org_perm("flows.flow_update"):
                return JsonResponse(
                    {"status": "failure", "description": _("You don't have permission to edit this flow")}, status=403
                )

            # try to parse our body
            definition = json.loads(force_str(request.body))
            try:
                flow = self.get_object(self.get_queryset())
                revision, issues = flow.save_revision(self.request.user, definition)
                return JsonResponse(
                    {
                        "status": "success",
                        "saved_on": json.encode_datetime(flow.saved_on, micros=True),
                        "revision": revision.as_json(),
                        "issues": [
                            *issues,
                            *Link.check_misstyped_links(flow, definition),
                            *Trigger.check_used_trigger_words(flow, definition),
                            *Attachment.validate_fields(flow.org, definition),
                        ],
                        "metadata": flow.metadata,
                    }
                )

            except FlowValidationException as e:
                error = _("Your flow failed validation. Please refresh your browser.")
                detail = str(e)
            except FlowVersionConflictException:
                error = _(
                    "Your flow has been upgraded to the latest version. "
                    "In order to continue editing, please refresh your browser."
                )
                detail = None
            except FlowUserConflictException as e:
                error = (
                    _(
                        "%s is currently editing this Flow. "
                        "Your changes will not be saved until you refresh your browser."
                    )
                    % e.other_user
                )
                detail = None
            except Exception as e:  # pragma: no cover
                import traceback

                traceback.print_stack(e)
                error = _("Your flow could not be saved. Please refresh your browser.")
                detail = None

            return JsonResponse({"status": "failure", "description": error, "detail": detail}, status=400)

    class ShowTemplates(OrgPermsMixin, SmartTemplateView):
        def get_queryset(self):
            org = self.request.user.get_org()
            return FlowTemplate.get_base_queryset(org)

        def get_groups(self):
            return (
                self.get_queryset()
                .values("group__name", "group__uuid")
                .annotate(total=Count("group__name"))
                .order_by("group__name")
            )

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            query_params = self.request.GET
            name_filter = query_params.get("q")
            group_filter = query_params.get("group")
            queryset = self.get_queryset()
            if name_filter:
                queryset = queryset.filter(name__icontains=name_filter)
            if group_filter:
                queryset = queryset.filter(group__uuid=group_filter)

            context["groups"] = self.get_groups()
            context["total"] = queryset.count()
            context["templates"] = queryset
            return context

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        class FlowCreateForm(BaseFlowForm):
            keyword_triggers = forms.CharField(
                required=False,
                label=_("Global keyword triggers"),
                help_text=_("When a user sends any of these keywords they will begin this flow"),
                widget=SelectWidget(
                    attrs={
                        "widget_only": False,
                        "multi": True,
                        "searchable": True,
                        "tags": True,
                        "space_select": True,
                        "placeholder": _("Select keywords to trigger this flow"),
                    }
                ),
            )

            flow_type = forms.ChoiceField(
                label=_("Type"),
                help_text=_("Choose the method for your flow"),
                choices=Flow.TYPE_CHOICES,
                widget=SelectWidget(attrs={"widget_only": False}),
            )

            def __init__(self, user, branding, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.user = user

                org = self.user.get_org()
                language_choices = languages.choices(org.flow_languages)

                # prune our type choices by brand config
                allowed_types = branding.get("flow_types")
                if allowed_types:
                    self.fields["flow_type"].choices = [c for c in Flow.TYPE_CHOICES if c[0] in allowed_types]

                self.fields["base_language"] = forms.ChoiceField(
                    label=_("Language"),
                    initial=org.flow_languages[0] if org.flow_languages else None,
                    choices=language_choices,
                    widget=SelectWidget(attrs={"widget_only": False}),
                )

            class Meta:
                model = Flow
                fields = ("name", "keyword_triggers", "flow_type", "base_language")
                widgets = {"name": InputWidget()}

        form_class = FlowCreateForm
        success_url = "uuid@flows.flow_editor"
        success_message = ""
        field_config = dict(name=dict(help=_("Choose a name to describe this flow, e.g. Demographic Survey")))

        def derive_exclude(self):
            user = self.request.user
            org = user.get_org()
            exclude = []

            if not org.flow_languages:
                exclude.append("base_language")

            return exclude

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            kwargs["branding"] = self.request.branding
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["has_flows"] = Flow.objects.filter(org=self.request.user.get_org(), is_active=True).count() > 0
            return context

        def save(self, obj):
            org = self.request.user.get_org()

            self.object = Flow.create(
                org,
                self.request.user,
                obj.name,
                flow_type=obj.flow_type,
                expires_after_minutes=Flow.EXPIRES_DEFAULTS[obj.flow_type],
                base_language=obj.base_language,
                create_revision=True,
                **dict(metadata={Flow.METADATA_IVR_RETRY: -1}),
            )

        def post_save(self, obj):
            user = self.request.user
            org = user.get_org()

            # create any triggers if user provided keywords
            if self.form.cleaned_data["keyword_triggers"]:
                keywords = self.form.cleaned_data["keyword_triggers"].split(",")
                for keyword in keywords:
                    Trigger.create(org, user, Trigger.TYPE_KEYWORD, flow=obj, keyword=keyword)

            return obj

    class Delete(AllowOnlyActiveFlowMixin, ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ("id",)
        cancel_url = "uuid@flows.flow_editor"
        success_message = ""
        submit_button_name = _("Delete")

        def get_success_url(self):
            return reverse("flows.flow_list")

        def post(self, request, *args, **kwargs):
            flow = self.get_object()
            self.object = flow

            flows = Flow.objects.filter(org=flow.org, flow_dependencies__in=[flow])
            if flows.count():
                return HttpResponseRedirect(smart_url(self.cancel_url, flow))

            # do the actual deletion
            flow.release(self.request.user)

            # we can't just redirect so as to make our modal do the right thing
            return self.render_modal_response()

    class Copy(OrgObjPermsMixin, SmartUpdateView):
        fields = []
        success_message = ""

        def form_valid(self, form):
            # copy our current object
            copy = Flow.copy(self.object, self.request.user)

            # redirect to the newly created flow
            return HttpResponseRedirect(reverse("flows.flow_editor", args=[copy.uuid]))

    class Update(AllowOnlyActiveFlowMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class BaseForm(BaseFlowForm):
            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.user = user

            class Meta:
                model = Flow
                fields = ("name",)
                widgets = {"name": InputWidget()}

        class SurveyForm(BaseForm):
            contact_creation = forms.ChoiceField(
                label=_("Create a contact "),
                help_text=_("Whether surveyor logins should be used as the contact for each run"),
                choices=((Flow.CONTACT_PER_RUN, _("For each run")), (Flow.CONTACT_PER_LOGIN, _("For each login"))),
                widget=SelectWidget(attrs={"widget_only": False}),
            )

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.fields["contact_creation"].initial = self.instance.metadata.get(
                    Flow.CONTACT_CREATION, Flow.CONTACT_PER_RUN
                )

            class Meta:
                model = Flow
                fields = ("name", "contact_creation")
                widgets = {"name": InputWidget()}

        class VoiceForm(BaseForm):
            ivr_retry = forms.ChoiceField(
                label=_("Retry call if unable to connect"),
                help_text=_("Retries call three times for the chosen interval"),
                initial=-1,
                choices=IVRCall.RETRY_CHOICES,
                widget=SelectWidget(attrs={"widget_only": False}),
            )
            expires_after_minutes = forms.ChoiceField(
                label=_("Expire inactive contacts"),
                help_text=_("When inactive contacts should be removed from the flow"),
                initial=Flow.EXPIRES_DEFAULTS[Flow.TYPE_VOICE],
                choices=Flow.EXPIRES_CHOICES[Flow.TYPE_VOICE],
                widget=SelectWidget(attrs={"widget_only": False}),
            )
            keyword_triggers = forms.CharField(
                required=False,
                label=_("Global keyword triggers"),
                help_text=_("When a user sends any of these keywords they will begin this flow"),
                widget=SelectWidget(
                    attrs={
                        "widget_only": False,
                        "multi": True,
                        "searchable": True,
                        "tags": True,
                        "space_select": True,
                        "placeholder": _("Keywords"),
                    }
                ),
            )

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

                metadata = self.instance.metadata

                # IVR retries
                ivr_retry = self.fields["ivr_retry"]
                ivr_retry.initial = metadata.get("ivr_retry", self.fields["ivr_retry"].initial)

                flow_triggers = Trigger.objects.filter(
                    org=self.instance.org,
                    flow=self.instance,
                    is_archived=False,
                    groups=None,
                    trigger_type=Trigger.TYPE_KEYWORD,
                ).order_by("created_on")

                keyword_triggers = self.fields["keyword_triggers"]
                keyword_triggers.initial = list([t.keyword for t in flow_triggers])

            class Meta:
                model = Flow
                fields = ("name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "ivr_retry")
                widgets = {"name": InputWidget(), "ignore_triggers": CheckboxWidget()}

        class MessagingForm(BaseForm):
            keyword_triggers = forms.CharField(
                required=False,
                label=_("Global keyword triggers"),
                help_text=_("When a user sends any of these keywords they will begin this flow"),
                widget=SelectWidget(
                    attrs={
                        "widget_only": False,
                        "multi": True,
                        "searchable": True,
                        "tags": True,
                        "space_select": True,
                        "placeholder": _("Keywords"),
                    }
                ),
            )

            expires_after_minutes = forms.ChoiceField(
                label=_("Expire inactive contacts"),
                help_text=_("When inactive contacts should be removed from the flow"),
                initial=Flow.EXPIRES_DEFAULTS[Flow.TYPE_MESSAGE],
                choices=Flow.EXPIRES_CHOICES[Flow.TYPE_MESSAGE],
                widget=SelectWidget(attrs={"widget_only": False}),
            )

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

                flow_triggers = Trigger.objects.filter(
                    org=self.instance.org,
                    flow=self.instance,
                    is_archived=False,
                    groups=None,
                    trigger_type=Trigger.TYPE_KEYWORD,
                ).order_by("created_on")

                keyword_triggers = self.fields["keyword_triggers"]
                keyword_triggers.initial = list([t.keyword for t in flow_triggers])

            class Meta:
                model = Flow
                fields = ("name", "keyword_triggers", "expires_after_minutes", "ignore_triggers")
                widgets = {"name": InputWidget(), "ignore_triggers": CheckboxWidget()}

        success_message = ""
        success_url = "uuid@flows.flow_editor"
        form_classes = {
            Flow.TYPE_MESSAGE: MessagingForm,
            Flow.TYPE_VOICE: VoiceForm,
            Flow.TYPE_SURVEY: SurveyForm,
            Flow.TYPE_BACKGROUND: BaseForm,
        }

        def get_form_class(self):
            return self.form_classes[self.object.flow_type]

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            metadata = obj.metadata

            if Flow.CONTACT_CREATION in self.form.cleaned_data:
                metadata[Flow.CONTACT_CREATION] = self.form.cleaned_data[Flow.CONTACT_CREATION]

            if "ivr_retry" in self.form.cleaned_data:
                metadata[Flow.METADATA_IVR_RETRY] = int(self.form.cleaned_data["ivr_retry"])

            obj.metadata = metadata
            return obj

        def post_save(self, obj):
            keywords = set()
            user = self.request.user
            org = user.get_org()

            if "keyword_triggers" in self.form.cleaned_data:
                # get existing keyword triggers for this flow
                existing = obj.triggers.filter(trigger_type=Trigger.TYPE_KEYWORD, is_archived=False, groups=None)
                existing_keywords = {t.keyword for t in existing}

                if len(self.form.cleaned_data["keyword_triggers"]) > 0:
                    keywords = set(self.form.cleaned_data["keyword_triggers"].split(","))

                removed_keywords = existing_keywords.difference(keywords)
                for keyword in removed_keywords:
                    obj.triggers.filter(keyword=keyword, groups=None, is_archived=False).update(is_archived=True)

                added_keywords = keywords.difference(existing_keywords)
                archived_keywords = [
                    t.keyword
                    for t in obj.triggers.filter(
                        org=org, flow=obj, trigger_type=Trigger.TYPE_KEYWORD, is_archived=True, groups=None
                    )
                ]

                # set difference does not have a deterministic order, we need to sort the keywords
                for keyword in sorted(added_keywords):
                    # first check if the added keyword is not amongst archived
                    if keyword in archived_keywords:  # pragma: needs cover
                        obj.triggers.filter(org=org, flow=obj, keyword=keyword, groups=None).update(is_archived=False)
                    else:
                        Trigger.objects.create(
                            org=org,
                            keyword=keyword,
                            trigger_type=Trigger.TYPE_KEYWORD,
                            flow=obj,
                            created_by=user,
                            modified_by=user,
                        )

            on_transaction_commit(lambda: update_session_wait_expires.delay(obj.pk))

            obj.update_related_flows()

            return obj

    class UploadActionRecording(OrgObjPermsMixin, SmartUpdateView):
        def post(self, request, *args, **kwargs):  # pragma: needs cover
            path = self.save_recording_upload(
                self.request.FILES["file"], self.request.POST.get("actionset"), self.request.POST.get("action")
            )
            return JsonResponse(dict(path=path))

        def save_recording_upload(self, file, actionset_id, action_uuid):  # pragma: needs cover
            flow = self.get_object()
            return private_file_storage.save(
                "recordings/%d/%d/steps/%s.wav" % (flow.org.pk, flow.id, action_uuid), file
            )

    class UploadMediaAction(OrgObjPermsMixin, SmartUpdateView):
        slug_url_kwarg = "uuid"

        def post(self, request, *args, **kwargs):
            return JsonResponse(self.save_media_upload(self.request.FILES["file"]))

        def save_media_upload(self, file):
            flow = self.get_object()
            random_uuid_folder_name = str(uuid4())
            extension = file.name.split(".")[-1]

            # browsers might send m4a files but correct MIME type is audio/mp4
            if extension == "m4a":
                file.content_type = "audio/mp4"

            public_url = private_file_storage.save_with_public_url(
                "attachments/%d/%d/steps/%s/%s" % (flow.org.pk, flow.id, random_uuid_folder_name, file.name),
                file,
            )
            return {"type": file.content_type, "url": public_url}

    class BaseList(SpaMixin, OrgFilterMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
        title = _("Flows")
        refresh = 10000
        fields = ("name", "modified_on")
        default_template = "flows/flow_list.html"
        default_order = ("-saved_on",)
        search_fields = ("name__icontains",)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org_has_flows"] = Flow.objects.filter(org=self.request.user.get_org(), is_active=True).count()
            context["folders"] = self.get_folders()
            context["labels"] = self.get_flow_labels()
            context["campaigns"] = self.get_campaigns()
            context["request_url"] = self.request.path

            # decorate flow objects with their run activity stats
            for flow in context["object_list"]:
                flow.run_stats = flow.get_run_stats()

            return context

        def derive_queryset(self, *args, **kwargs):
            qs = super().derive_queryset(*args, **kwargs)
            return qs.exclude(is_system=True).exclude(is_active=False)

        def get_campaigns(self):
            from temba.campaigns.models import CampaignEvent

            org = self.request.user.get_org()
            events = CampaignEvent.objects.filter(
                campaign__org=org,
                is_active=True,
                campaign__is_active=True,
                flow__is_archived=False,
                flow__is_active=True,
                flow__is_system=False,
            )
            return (
                events.values("campaign__name", "campaign__id").annotate(count=Count("id")).order_by("campaign__name")
            )

        def apply_bulk_action(self, user, action, objects, label):
            super().apply_bulk_action(user, action, objects, label)

            if action == "archive":
                ignored = objects.filter(is_archived=False)
                if ignored:
                    flow_names = ", ".join([f.name for f in ignored])
                    raise forms.ValidationError(
                        _("The following flows are still used by campaigns so could not be archived: %(flows)s"),
                        params={"flows": flow_names},
                    )

        def get_bulk_action_labels(self):
            return self.get_user().get_org().flow_labels.all()

        def get_flow_labels(self):
            labels = []
            for label in FlowLabel.objects.filter(org=self.request.user.get_org(), parent=None):
                labels.append(
                    dict(
                        pk=label.pk,
                        uuid=label.uuid,
                        label=label.name,
                        count=label.get_flows_count(),
                        children=label.children.all(),
                    )
                )
            return labels

        def get_folders(self):
            org = self.request.user.get_org()

            return [
                dict(
                    label="Active",
                    url=reverse("flows.flow_list"),
                    count=Flow.objects.exclude(is_system=True)
                    .filter(is_active=True, is_archived=False, org=org)
                    .count(),
                ),
                dict(
                    label="Archived",
                    url=reverse("flows.flow_archived"),
                    count=Flow.objects.exclude(is_system=True)
                    .filter(is_active=True, is_archived=True, org=org)
                    .count(),
                ),
            ]

        def get_gear_links(self):
            links = []

            if self.has_org_perm("orgs.org_import"):
                links.append(dict(title=_("Import"), href=reverse("orgs.org_import")))

            if self.has_org_perm("orgs.org_export"):
                links.append(dict(title=_("Export"), href=reverse("orgs.org_export")))

            if self.has_org_perm("flows.flowtemplate_create"):
                links.append(
                    dict(
                        title=_("Create Template"),
                        id="flow-template-create",
                        href=reverse("flows.flowtemplate_create"),
                    )
                )

                links.append(
                    dict(
                        title=_("Template List"),
                        id="flow-template-create",
                        href=reverse("flows.flowtemplate_list"),
                    )
                )

            return links

    class Archived(BaseList):
        bulk_actions = ("restore",)
        default_order = ("-created_on",)

        def derive_queryset(self, *args, **kwargs):
            return super().derive_queryset(*args, **kwargs).filter(is_active=True, is_archived=True)

    class List(BaseList):
        title = _("Flows")
        bulk_actions = ("archive", "label")

        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            queryset = queryset.filter(is_active=True, is_archived=False)
            return queryset

    class Campaign(BaseList, OrgObjPermsMixin):
        bulk_actions = ("label",)
        campaign = None

        def has_permission_view_objects(self):
            from temba.campaigns.models import Campaign

            campaign = Campaign.objects.filter(
                org=self.request.user.get_org(), id=self.kwargs.get("campaign_id")
            ).first()
            if not campaign:
                raise PermissionDenied()
            return None

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<campaign_id>\d+)/$" % (path, action)

        def derive_title(self, *args, **kwargs):
            return self.get_campaign().name

        def get_object_org(self):
            from temba.campaigns.models import Campaign

            return Campaign.objects.get(pk=self.kwargs["campaign_id"]).org

        def get_campaign(self):
            if not self.campaign:
                from temba.campaigns.models import Campaign

                campaign_id = self.kwargs["campaign_id"]
                self.campaign = Campaign.objects.filter(id=campaign_id, org=self.request.user.get_org()).first()
            return self.campaign

        def get_queryset(self, **kwargs):
            from temba.campaigns.models import CampaignEvent

            flow_ids = CampaignEvent.objects.filter(
                campaign=self.get_campaign(), flow__is_archived=False, flow__is_system=False, is_active=True
            ).values("flow__id")

            flows = Flow.objects.filter(id__in=flow_ids, org=self.request.user.get_org()).order_by("-modified_on")
            return flows

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["current_campaign"] = self.get_campaign()
            return context

    class Filter(BaseList, OrgObjPermsMixin):
        add_button = True
        bulk_actions = ("label",)
        slug_url_kwarg = "uuid"

        def has_permission_view_objects(self):
            flow_label = FlowLabel.objects.filter(
                org=self.request.user.get_org(), id=self.kwargs.get("label_id")
            ).first()
            if not flow_label:
                raise PermissionDenied()
            return None

        def get_gear_links(self):
            links = []

            label = FlowLabel.objects.get(uuid=self.kwargs["uuid"])

            if self.has_org_perm("flows.flow_update"):
                # links.append(dict(title=_("Edit"), href="#", js_class="label-update-btn"))

                links.append(
                    dict(
                        id="update-label",
                        title=_("Edit"),
                        style="button-primary",
                        href=f"{reverse('flows.flowlabel_update', args=[label.pk])}",
                        modax=_("Edit Label"),
                    )
                )

            if self.has_org_perm("flows.flow_delete"):
                links.append(
                    dict(
                        id="delete-label",
                        title=_("Delete Label"),
                        href=f"{reverse('flows.flowlabel_delete', args=[label.pk])}",
                        modax=_("Delete Label"),
                    )
                )

            return links

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["current_label"] = self.derive_label()
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<uuid>[0-9a-f-]+)/$" % (path, action)

        def derive_title(self, *args, **kwargs):
            return self.derive_label().name

        def get_object_org(self):
            return FlowLabel.objects.get(uuid=self.kwargs["uuid"]).org

        def derive_label(self):
            return FlowLabel.objects.get(uuid=self.kwargs["uuid"], org=self.request.user.get_org())

        def get_label_filter(self):
            label = FlowLabel.objects.get(uuid=self.kwargs["uuid"])
            children = label.children.all()
            if children:  # pragma: needs cover
                return [lb for lb in FlowLabel.objects.filter(parent=label)] + [label]
            else:
                return [label]

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            qs = qs.filter(org=self.request.user.get_org()).order_by("-created_on")
            qs = qs.filter(labels__in=self.get_label_filter(), is_archived=False).distinct()

            return qs

    class Editor(SpaMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def derive_title(self):
            return self.object.name

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)

            context["media_url"] = "%s://%s/" % ("http" if settings.DEBUG else "https", settings.AWS_BUCKET_DOMAIN)
            context["pdf_export_lang"] = self.request.GET.get("pdf_export_lang", None)

            dev_mode = getattr(settings, "EDITOR_DEV_MODE", False)
            prefix = "/dev" if dev_mode else settings.STATIC_URL

            # get our list of assets to include
            scripts = []
            styles = []

            if dev_mode:  # pragma: no cover
                response = requests.get("http://localhost:3000/asset-manifest.json")
                data = response.json()
            else:
                with open("node_modules/@greatnonprofits-nfp/flow-editor/build/asset-manifest.json") as json_file:
                    data = json.load(json_file)

            for key, filename in data.get("files").items():

                # tack on our prefix for dev mode
                filename = prefix + filename

                # ignore precache manifest
                if key.startswith("precache-manifest") or key.startswith("service-worker"):
                    continue

                # css files
                if key.endswith(".css") and filename.endswith(".css"):
                    styles.append(filename)

                # javascript
                if key.endswith(".js") and filename.endswith(".js"):
                    scripts.append(filename)

            flow = self.object

            context["scripts"] = scripts
            context["styles"] = styles
            context["migrate"] = "migrate" in self.request.GET

            if flow.is_archived:
                context["mutable"] = False
                context["can_start"] = False
                context["can_simulate"] = False
            else:
                context["mutable"] = self.has_org_perm("flows.flow_update") and not self.request.user.is_superuser
                context["can_start"] = flow.flow_type != Flow.TYPE_VOICE or flow.org.supports_ivr()
                context["can_simulate"] = True

            context["dev_mode"] = dev_mode
            context["is_starting"] = flow.is_starting()
            context["feature_filters"] = json.dumps(self.get_features(flow.org, flow))

            # check if there is no other users that edititing current flow
            # then make this user as main editor and set expiration time of editing to this user
            r = get_redis_connection()
            flow_key = f"active-flow-editor-{flow.uuid}"
            active_flow_editor = r.get(flow_key)
            if active_flow_editor is not None:
                active_editor_email = active_flow_editor.decode()
                if active_editor_email == self.request.user.username:
                    return context

                context["mutable"] = False
                context["immutable_alert"] = (
                    _("%s is currently editing this Flow. You can open this flow only in view mode.")
                    % active_editor_email
                )
            else:
                r.set(flow_key, self.request.user.username, ex=30)

            return context

        def get_features(self, org, flow=None) -> list:
            features = []

            facebook_channel = org.get_channel(Channel.ROLE_SEND, scheme=URN.FACEBOOK_SCHEME)
            whatsapp_channel = org.get_channel(Channel.ROLE_SEND, scheme=URN.WHATSAPP_SCHEME)

            if facebook_channel:
                features.append("facebook")
            if whatsapp_channel:
                features.append("whatsapp")
            if org.get_integrations(IntegrationType.Category.AIRTIME):
                features.append("airtime")
            if org.classifiers.filter(is_active=True).exists():
                features.append("classifier")
            if org.ticketers.filter(is_active=True).exists():
                features.append("ticketer")
            if org.get_resthooks():
                features.append("resthook")
            if org.country_id:
                features.append("locations")
            if org.is_ivr_machine_detection_enabled():
                features.append("machine_detection")
            if flow is not None and flow.flow_type != Flow.TYPE_MESSAGE:
                features.append("spell_checker")

            return features

        def get_gear_links(self):
            links = []
            flow = self.object
            if (
                flow.flow_type != Flow.TYPE_SURVEY
                and self.has_org_perm("flows.flow_broadcast")
                and not flow.is_archived
            ):
                links.append(
                    dict(
                        id="launch-flow",
                        title=_("Launch Flow"),
                        style="button-primary",
                        href=f"{reverse('flows.flow_launch', args=[self.object.pk])}",
                        modax=_("Launch Flow"),
                    )
                )

            if self.has_org_perm("flows.flow_results"):
                links.append(
                    dict(
                        title=_("Results"),
                        style="button-primary",
                        href=reverse("flows.flow_results", args=[flow.uuid]),
                    )
                )
            if len(links) > 1:
                links.append(dict(divider=True))

            if self.has_org_perm("flows.flow_update") and not flow.is_archived:
                links.append(
                    dict(
                        id="edit-flow",
                        title=_("Edit"),
                        href=f"{reverse('flows.flow_update', args=[self.object.pk])}",
                        modax=_("Edit Flow"),
                    )
                )

            if self.has_org_perm("flows.flow_copy"):
                links.append(dict(title=_("Copy"), posterize=True, href=reverse("flows.flow_copy", args=[flow.id])))

            if self.has_org_perm("flows.flow_results") and flow.flow_type == Flow.TYPE_MESSAGE:
                links.append(
                    dict(
                        title=_("Download Images"),
                        style="btn-primary",
                        href=reverse("flows.flowimage_filter", args=["flow", flow.uuid]),
                    )
                )

            if self.has_org_perm("orgs.org_lookups") and flow.flow_type in [Flow.TYPE_MESSAGE, Flow.TYPE_VOICE]:
                links.append(dict(title=_("Import Database"), href=reverse("orgs.org_lookups")))

            if self.has_org_perm("flows.flow_delete"):
                links.append(
                    dict(
                        id="delete-flow",
                        title=_("Delete"),
                        href=f"{reverse('flows.flow_delete', args=[self.object.pk])}",
                        modax=_("Delete Flow"),
                    )
                )
            if self.has_org_perm("flows.flowtemplate_create_from_flow"):
                links.append(dict(divider=True)),
                links.append(
                    dict(
                        id="create-template-flow",
                        title=_("Copy as template"),
                        href=f"{reverse('flows.flowtemplate_create_from_flow', args=[self.object.pk])}",
                        modax=_(f'Copy "{self.object.name}" as template'),
                    )
                )
            links.append(dict(divider=True)),

            if self.has_org_perm("orgs.org_export"):
                links.append(dict(title=_("Export Definition"), href=f"{reverse('orgs.org_export')}?flow={flow.id}"))

            # limit PO export/import to non-archived flows since mailroom doesn't know about archived flows
            if not self.object.is_archived:
                if self.has_org_perm("flows.flow_export_translation"):
                    links.append(
                        dict(
                            id="export-translation",
                            title=_("Export Translation"),
                            href=f"{reverse('flows.flow_export_translation', args=[self.object.pk])}",
                            modax=_("Export Translation"),
                        )
                    )

                if self.has_org_perm("flows.flow_import_translation"):
                    links.append(
                        dict(
                            title=_("Import Translation"),
                            href=reverse("flows.flow_import_translation", args=[flow.id]),
                        )
                    )

            if self.has_org_perm("flows.flow_monitoring"):
                links.append(
                    dict(
                        id="Monitoring",
                        title=_("Monitoring"),
                        href=reverse("flows.flow_monitoring", args=[self.object.pk]),
                    )
                )

            user = self.get_user()
            if user.is_superuser or user.is_staff:
                links.append(
                    dict(
                        title=_("Service"),
                        posterize=True,
                        href=f'{reverse("orgs.org_service")}?organization={flow.org_id}&redirect_url={reverse("flows.flow_editor", args=[flow.uuid])}',
                    )
                )

            return links

        def get_mergeable_flows(self):
            queryset = Flow.objects.filter(org=self.object.org, is_active=True, is_archived=False, is_system=False)
            queryset = queryset.filter(flow_type=self.object.flow_type)
            queryset = queryset.exclude(version_number__in=legacy.VERSIONS)
            queryset = queryset.exclude(uuid=self.object.uuid).order_by("name")
            return queryset

    class ExportPdf(OrgObjPermsMixin, SmartReadView):
        permission = "flows.flow_pdf_export"
        slug_url_kwarg = "uuid"

        def derive_title(self):
            return self.object.name

        def get_template_names(self):
            return "flows/flow_export_pdf.haml"

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)

            context["media_url"] = "%s://%s/" % ("http" if settings.DEBUG else "https", settings.AWS_BUCKET_DOMAIN)
            context["pdf_export_lang"] = self.request.GET.get("pdf_export_lang", None)

            dev_mode = getattr(settings, "EDITOR_DEV_MODE", False)
            prefix = "/dev" if dev_mode else settings.STATIC_URL

            # get our list of assets to incude
            scripts = []
            styles = []

            if dev_mode:  # pragma: no cover
                response = requests.get("http://localhost:3000/asset-manifest.json")
                data = response.json()
            else:
                with open("node_modules/@greatnonprofits-nfp/flow-editor/build/asset-manifest.json") as json_file:
                    data = json.load(json_file)

            for key, filename in data.get("files").items():

                # tack on our prefix for dev mode
                filename = prefix + filename

                # ignore precache manifest
                if key.startswith("precache-manifest") or key.startswith("service-worker"):
                    continue

                # css files
                if key.endswith(".css") and filename.endswith(".css"):
                    styles.append(filename)

                # javascript
                if key.endswith(".js") and filename.endswith(".js"):
                    scripts.append(filename)

            flow = self.object

            context["scripts"] = scripts
            context["styles"] = styles
            context["migrate"] = "migrate" in self.request.GET

            if flow.is_archived:
                context["mutable"] = False
                context["can_start"] = False
                context["can_simulate"] = False
            else:
                context["mutable"] = self.has_org_perm("flows.flow_update") and not self.request.user.is_superuser
                context["can_start"] = flow.flow_type != Flow.TYPE_VOICE or flow.org.supports_ivr()
                context["can_simulate"] = True

            context["dev_mode"] = dev_mode
            context["is_starting"] = flow.is_starting()

            return context

    class ChangeLanguage(OrgObjPermsMixin, SmartUpdateView):
        class Form(forms.Form):
            language = forms.CharField(required=True)

            def __init__(self, user, instance, *args, **kwargs):
                self.user = user

                super().__init__(*args, **kwargs)

            def clean_language(self):
                data = self.cleaned_data["language"]
                if data and data not in self.user.get_org().flow_languages:
                    raise ValidationError(_("Not a valid language."))

                return data

        form_class = Form
        success_url = "uuid@flows.flow_editor"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def form_valid(self, form):
            flow_def = mailroom.get_client().flow_change_language(
                self.object.get_definition(), form.cleaned_data["language"]
            )

            self.object.save_revision(self.get_user(), flow_def)

            return HttpResponseRedirect(self.get_success_url())

    class ExportTranslation(OrgObjPermsMixin, ModalMixin, SmartUpdateView):
        class Form(forms.Form):
            language = forms.ChoiceField(
                required=False,
                label=_("Language"),
                help_text=_("Include translations in this language."),
                choices=(("", "None"),),
                widget=SelectWidget(),
            )

            def __init__(self, user, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)

                org = user.get_org()

                self.user = user
                self.fields["language"].choices += languages.choices(codes=org.flow_languages)

        form_class = Form
        submit_button_name = _("Export")
        success_url = "@flows.flow_list"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def form_valid(self, form):
            params = {"flow": self.object.id, "language": form.cleaned_data["language"]}
            download_url = reverse("flows.flow_download_translation") + "?" + urlencode(params, doseq=True)

            # if this is an XHR request, we need to return a structured response that it can parse
            if "HTTP_X_PJAX" in self.request.META:
                response = self.render_modal_response(form)
                response["Temba-Success"] = download_url
                return response

            return HttpResponseRedirect(download_url)

    class DownloadTranslation(OrgPermsMixin, SmartListView):
        """
        Download link for PO translation files extracted from flows by mailroom
        """

        def get(self, request, *args, **kwargs):
            org = self.request.org
            flow_ids = self.request.GET.getlist("flow")
            flows = org.flows.filter(id__in=flow_ids, is_active=True)
            if len(flows) != len(flow_ids):
                raise Http404()

            language = request.GET.get("language", "")
            filename = slugify_with(flows[0].name) if len(flows) == 1 else "flows"
            if language:
                filename += f".{language}"
            filename += ".po"

            po = Flow.export_translation(org, flows, language)

            response = HttpResponse(po, content_type="text/x-gettext-translation")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response

    class ImportTranslation(OrgObjPermsMixin, SmartUpdateView):
        class UploadForm(forms.Form):
            po_file = forms.FileField(label=_("PO translation file"), required=True)

            def __init__(self, user, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.flow = instance

            def clean_po_file(self):
                data = self.cleaned_data["po_file"]
                if data:
                    try:
                        po_info = gettext.po_get_info(data.read().decode())
                    except Exception:
                        raise ValidationError(_("File doesn't appear to be a valid PO file."))

                    if po_info.language_code:
                        if po_info.language_code == self.flow.base_language:
                            raise ValidationError(
                                _("Contains translations in %(lang)s which is the base language of this flow."),
                                params={"lang": po_info.language_name},
                            )

                        if po_info.language_code not in self.flow.org.flow_languages:
                            raise ValidationError(
                                _("Contains translations in %(lang)s which is not a supported translation language."),
                                params={"lang": po_info.language_name},
                            )

                return data

        class ConfirmForm(forms.Form):
            language = forms.ChoiceField(
                label=_("Language"),
                help_text=_("Replace flow translations in this language."),
                required=True,
                widget=SelectWidget(),
            )

            def __init__(self, user, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)

                org = user.get_org()
                lang_codes = list(org.flow_languages)
                lang_codes.remove(instance.base_language)

                self.fields["language"].choices = languages.choices(codes=lang_codes)

        title = _("Import Translation")
        submit_button_name = _("Import")
        success_url = "uuid@flows.flow_editor"

        def get_form_class(self):
            return self.ConfirmForm if self.request.GET.get("po") else self.UploadForm

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def form_valid(self, form):
            org = self.request.user.get_org()
            po_uuid = self.request.GET.get("po")

            if not po_uuid:
                po_file = form.cleaned_data["po_file"]
                po_uuid = gettext.po_save(org, po_file)

                return HttpResponseRedirect(
                    reverse("flows.flow_import_translation", args=[self.object.id]) + f"?po={po_uuid}"
                )
            else:
                po_data = gettext.po_load(org, po_uuid)
                language = form.cleaned_data["language"]

                updated_defs = Flow.import_translation(self.object.org, [self.object], language, po_data)
                self.object.save_revision(self.request.user, updated_defs[str(self.object.uuid)])

                analytics.track(self.request.user, "temba.flow_po_imported")

            return HttpResponseRedirect(self.get_success_url())

        @cached_property
        def po_info(self):
            po_uuid = self.request.GET.get("po")
            if not po_uuid:
                return None

            org = self.request.user.get_org()
            po_data = gettext.po_load(org, po_uuid)
            return gettext.po_get_info(po_data)

        def get_context_data(self, *args, **kwargs):
            flow_lang_code = self.object.base_language

            context = super().get_context_data(*args, **kwargs)
            context["show_upload_form"] = not self.po_info
            context["po_info"] = self.po_info
            context["flow_language"] = {"iso_code": flow_lang_code, "name": languages.get_name(flow_lang_code)}
            return context

        def derive_initial(self):
            return {"language": self.po_info.language_code if self.po_info else ""}

    class ExportResults(ModalMixin, OrgPermsMixin, SmartFormView):
        class ExportForm(forms.Form):
            flows = forms.ModelMultipleChoiceField(
                Flow.objects.filter(id__lt=0), required=True, widget=forms.MultipleHiddenInput()
            )

            group_memberships = forms.ModelMultipleChoiceField(
                queryset=ContactGroup.user_groups.none(),
                required=False,
                label=_("Groups"),
                widget=SelectMultipleWidget(attrs={"placeholder": _("Optional: Group memberships")}),
            )

            contact_fields = forms.ModelMultipleChoiceField(
                ContactField.user_fields.filter(id__lt=0),
                required=False,
                label=("Fields"),
                widget=SelectMultipleWidget(
                    attrs={"placeholder": _("Optional: Fields to include"), "searchable": True}
                ),
            )

            extra_urns = forms.MultipleChoiceField(
                required=False,
                label=_("URNs"),
                choices=URN.SCHEME_CHOICES,
                widget=SelectMultipleWidget(
                    attrs={"placeholder": _("Optional: URNs in addition to the one used in the flow")}
                ),
            )

            responded_only = forms.BooleanField(
                required=False,
                label=_("Responded Only"),
                initial=True,
                help_text=_("Only export results for contacts which responded"),
                widget=CheckboxWidget(),
            )

            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.user = user
                self.fields[ExportFlowResultsTask.CONTACT_FIELDS].queryset = ContactField.user_fields.active_for_org(
                    org=self.user.get_org()
                ).order_by(Lower("label"))

                self.fields[ExportFlowResultsTask.GROUP_MEMBERSHIPS].queryset = ContactGroup.user_groups.filter(
                    org=self.user.get_org(), is_active=True, status=ContactGroup.STATUS_READY
                ).order_by(Lower("name"))

                self.fields[ExportFlowResultsTask.FLOWS].queryset = Flow.objects.filter(
                    org=self.user.get_org(), is_active=True
                )

            def clean(self):
                cleaned_data = super().clean()

                if (
                    ExportFlowResultsTask.CONTACT_FIELDS in cleaned_data
                    and len(cleaned_data[ExportFlowResultsTask.CONTACT_FIELDS])
                    > ExportFlowResultsTask.MAX_CONTACT_FIELDS_COLS
                ):  # pragma: needs cover
                    raise forms.ValidationError(
                        _(
                            f"You can only include up to {ExportFlowResultsTask.MAX_CONTACT_FIELDS_COLS} contact fields in your export"
                        )
                    )

                if (
                    ExportFlowResultsTask.GROUP_MEMBERSHIPS in cleaned_data
                    and len(cleaned_data[ExportFlowResultsTask.GROUP_MEMBERSHIPS])
                    > ExportFlowResultsTask.MAX_GROUP_MEMBERSHIPS_COLS
                ):  # pragma: needs cover
                    raise forms.ValidationError(
                        _(
                            f"You can only include up to {ExportFlowResultsTask.MAX_GROUP_MEMBERSHIPS_COLS} groups for group memberships in your export"
                        )
                    )

                try:
                    cleaned_data["extra_queries"] = json.loads(cleaned_data.get("extra_queries") or "{}")
                except JSONDecodeError:
                    cleaned_data["extra_queries"] = {}

                return cleaned_data

        form_class = ExportForm
        submit_button_name = _("Download")
        success_url = "@flows.flow_list"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def derive_initial(self):
            flow_ids = self.request.GET.get("ids", None)
            if flow_ids:  # pragma: needs cover
                return dict(
                    flows=Flow.objects.filter(
                        org=self.request.user.get_org(), is_active=True, id__in=flow_ids.split(",")
                    )
                )
            else:
                return dict()

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            # is there already an export taking place?
            existing = ExportFlowResultsTask.get_recent_unfinished(org)
            if existing:
                messages.info(
                    self.request,
                    _(
                        "There is already an export in progress, started by %s. You must wait "
                        "for that export to complete before starting another." % existing.created_by.username
                    ),
                )
            else:
                flows = form.cleaned_data[ExportFlowResultsTask.FLOWS]
                responded_only = form.cleaned_data[ExportFlowResultsTask.RESPONDED_ONLY]

                export = ExportFlowResultsTask.create(
                    org,
                    user,
                    flows,
                    contact_fields=form.cleaned_data[ExportFlowResultsTask.CONTACT_FIELDS],
                    responded_only=responded_only,
                    extra_urns=form.cleaned_data[ExportFlowResultsTask.EXTRA_URNS],
                    group_memberships=form.cleaned_data[ExportFlowResultsTask.GROUP_MEMBERSHIPS],
                    extra_queries=form.cleaned_data[ExportFlowResultsTask.EXTRA_QUERIES],
                )
                on_transaction_commit(lambda: export_flow_results_task.delay(export.pk))

                analytics.track(
                    self.request.user,
                    "temba.responses_export_started" if responded_only else "temba.results_export_started",
                    dict(flows=", ".join([f.uuid for f in flows])),
                )

                if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):  # pragma: needs cover
                    messages.info(
                        self.request,
                        _("We are preparing your export. We will e-mail you at %s when it is ready.")
                        % self.request.user.username,
                    )

                else:
                    export = ExportFlowResultsTask.objects.get(id=export.id)
                    dl_url = reverse("assets.download", kwargs=dict(type="results_export", pk=export.id))
                    messages.info(
                        self.request,
                        _("Export complete, you can find it here: %s (production users will get an email)") % dl_url,
                    )

            if "HTTP_X_PJAX" not in self.request.META:
                return HttpResponseRedirect(self.get_success_url())
            else:  # pragma: no cover
                response = self.render_modal_response(form)
                response["REDIRECT"] = self.get_success_url()
                return response

    class ActivityChart(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        """
        Intercooler helper that renders a chart of activity by a given period
        """

        # the min number of responses to show a histogram
        HISTOGRAM_MIN = 0

        # the min number of responses to show the period charts
        PERIOD_MIN = 0

        EXIT_TYPES = {
            None: "active",
            FlowRun.EXIT_TYPE_COMPLETED: "completed",
            FlowRun.EXIT_TYPE_INTERRUPTED: "interrupted",
            FlowRun.EXIT_TYPE_EXPIRED: "expired",
            FlowRun.EXIT_TYPE_FAILED: "failed",
        }

        def get_context_data(self, *args, **kwargs):

            total_responses = 0
            context = super().get_context_data(*args, **kwargs)

            flow = self.get_object()
            from temba.flows.models import FlowPathCount

            from_uuids = flow.metadata["waiting_exit_uuids"]
            dates = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).aggregate(
                Max("period"), Min("period")
            )
            start_date = dates.get("period__min")
            end_date = dates.get("period__max")

            # by hour of the day
            hod = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).extra(
                {"hour": "extract(hour from period::timestamp)"}
            )
            hod = hod.values("hour").annotate(count=Sum("count")).order_by("hour")
            hod_dict = {int(h.get("hour")): h.get("count") for h in hod}

            hours = []
            for x in range(0, 24):
                hours.append({"bucket": datetime(1970, 1, 1, hour=x), "count": hod_dict.get(x, 0)})

            # by day of the week
            dow = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).extra(
                {"day": "extract(dow from period::timestamp)"}
            )
            dow = dow.values("day").annotate(count=Sum("count"))
            dow_dict = {int(d.get("day")): d.get("count") for d in dow}

            dow = []
            for x in range(0, 7):
                day_count = dow_dict.get(x, 0)
                dow.append({"day": x, "count": day_count})
                total_responses += day_count

            if total_responses > self.PERIOD_MIN:
                dow = sorted(dow, key=lambda k: k["day"])
                days = (
                    _("Sunday"),
                    _("Monday"),
                    _("Tuesday"),
                    _("Wednesday"),
                    _("Thursday"),
                    _("Friday"),
                    _("Saturday"),
                )
                dow = [
                    {
                        "day": days[d["day"]],
                        "count": d["count"],
                        "pct": 100 * float(d["count"]) / float(total_responses),
                    }
                    for d in dow
                ]
                context["dow"] = dow
                context["hod"] = hours

            if total_responses > self.HISTOGRAM_MIN:
                # our main histogram
                date_range = end_date - start_date
                histogram = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids)
                if date_range < timedelta(days=21):
                    histogram = histogram.extra({"bucket": "date_trunc('hour', period)"})
                    min_date = start_date - timedelta(hours=1)
                elif date_range < timedelta(days=500):
                    histogram = histogram.extra({"bucket": "date_trunc('day', period)"})
                    min_date = end_date - timedelta(days=100)
                else:
                    histogram = histogram.extra({"bucket": "date_trunc('week', period)"})
                    min_date = end_date - timedelta(days=500)

                histogram = histogram.values("bucket").annotate(count=Sum("count")).order_by("bucket")
                context["histogram"] = histogram

                # highcharts works in UTC, but we want to offset our chart according to the org timezone
                context["min_date"] = min_date

            counts = FlowRunCount.objects.filter(flow=flow).values("exit_type").annotate(Sum("count"))

            total_runs = 0
            for count in counts:
                key = self.EXIT_TYPES[count["exit_type"]]
                context[key] = count["count__sum"]
                total_runs += count["count__sum"]

            # make sure we have a value for each one
            for state in ("expired", "interrupted", "completed", "active", "failed"):
                if state not in context:
                    context[state] = 0

            context["total_runs"] = total_runs
            context["total_responses"] = total_responses

            return context

    class RunTable(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        """
        Intercooler helper which renders rows of runs to be embedded in an existing table with infinite scrolling
        """

        paginate_by = 50

        @classmethod
        def search_query(cls, query, base_queryset):
            from .search.parser import FlowRunSearch

            runs_search = FlowRunSearch(query=query, base_queryset=base_queryset)
            return runs_search.search()

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            flow = self.get_object()

            after = self.request.GET.get("after")
            before = self.request.GET.get("before")
            search_query = self.request.GET.get("q")
            contact_query = self.request.GET.get("contact_query")

            runs = flow.runs.exclude(contact__is_active=False)

            if after:
                after = flow.org.parse_datetime(after)
                if after:
                    runs = runs.filter(modified_on__gte=after)
                else:
                    runs = runs.filter(id=-1)

            if before:
                before = flow.org.parse_datetime(before)
                if before:
                    runs = runs.filter(modified_on__lte=before)
                else:
                    runs = runs.filter(id=-1)

            if search_query:
                runs, query_error = FlowCRUDL.RunTable.search_query(query=search_query, base_queryset=runs)
                if query_error:
                    context["query_error"] = query_error

            if contact_query:
                try:
                    org = flow.org
                    contact_ids = query_contact_ids(org, contact_query, active_only=False)
                    runs = runs.filter(contact_id__in=contact_ids)
                except SearchException as e:
                    context["query_error"] = e.message

            if str_to_bool(self.request.GET.get("responded", "true")):
                runs = runs.filter(responded=True)

            context["total_runs"] = len(runs)

            # paginate
            modified_on = self.request.GET.get("modified_on", None)
            if modified_on:
                id = self.request.GET["id"]

                modified_on = iso8601.parse_date(modified_on)
                runs = runs.filter(modified_on__lte=modified_on).exclude(id=id)

            # we grab one more than our page to denote whether there's more to get
            runs = list(runs.order_by("-modified_on")[: self.paginate_by + 1])
            context["more"] = len(runs) > self.paginate_by
            runs = runs[: self.paginate_by]

            result_fields = flow.metadata["results"]

            # populate result values
            for run in runs:
                results = run.results
                run.value_list = []
                for result_field in result_fields:
                    run.value_list.append(results.get(result_field["key"], None))

            context["runs"] = runs
            context["start_date"] = flow.org.get_delete_date(archive_type=Archive.TYPE_FLOWRUN)
            context["paginate_by"] = self.paginate_by
            return context

    class LinksTable(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        """
        Intercooler helper which renders rows of trackable links to be embedded in an existing table with infinite scrolling
        """

        paginate_by = 50

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            flow = self.get_object()
            additional_filters = {}
            if flow.is_archived:
                additional_filters["created_on__lt"] = flow.modified_on
            links = LinkContacts.objects.filter(link__related_flow=flow, is_active=True, **additional_filters)

            # paginate
            modified_on = self.request.GET.get("modified_on", None)
            if modified_on:
                id = self.request.GET["id"]

                modified_on = iso8601.parse_date(modified_on)
                links = links.filter(modified_on__lte=modified_on).exclude(id=id)

            # we grab one more than our page to denote whether there's more to get
            links = list(links.order_by("-modified_on")[: self.paginate_by + 1])
            context["more"] = len(links) > self.paginate_by
            links = links[: self.paginate_by]

            context["trackable_links"] = links
            context["start_date"] = flow.org.get_delete_date(archive_type=Archive.TYPE_FLOWRUN)
            context["paginate_by"] = self.paginate_by
            return context

    class CategoryCounts(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def render_to_response(self, context, **response_kwargs):
            return JsonResponse(self.get_object().get_category_counts())

    class Results(SpaMixin, AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get_gear_links(self):
            links = []

            if self.has_org_perm("flows.flow_results"):
                links.append(
                    dict(
                        id="download-results",
                        title=_("Download"),
                        modax=_("Download Results"),
                        href=f"{reverse('flows.flow_export_results')}?ids={self.get_object().pk}",
                    )
                )

            if self.has_org_perm("flows.flow_editor"):
                links.append(
                    dict(
                        title=_("Edit Flow"),
                        style="button-primary",
                        href=reverse("flows.flow_editor", args=[self.get_object().uuid]),
                    )
                )

            return links

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            flow = self.get_object()

            result_fields = []
            for result_field in flow.metadata[Flow.METADATA_RESULTS]:
                if not result_field["name"].startswith("_"):
                    result_field = result_field.copy()
                    result_field["has_categories"] = "true" if len(result_field["categories"]) > 1 else "false"
                    result_fields.append(result_field)
            context["result_fields"] = result_fields

            context["categories"] = flow.get_category_counts()["counts"]
            context["utcoffset"] = int(datetime.now(flow.org.timezone).utcoffset().total_seconds() // 60)
            context["trackable_links"] = LinkContacts.objects.filter(link__related_flow=flow).exists()

            contact_query = self.request.GET.get("contact_query")
            if contact_query:
                try:
                    parsed_query = parse_query(flow.org, contact_query)
                    context["contact_query"] = parsed_query.query
                except SearchException:
                    context["contact_query"] = contact_query

            return context

    class Activity(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get(self, request, *args, **kwargs):
            flow = self.get_object(self.get_queryset())
            (active, visited) = flow.get_activity()

            # update expiration time of editing for active editor
            r = get_redis_connection()
            flow_key = f"active-flow-editor-{flow.uuid}"
            active_editor = r.get(flow_key)
            editing_available = False
            if active_editor is not None:
                if self.request.user.username == active_editor.decode():
                    editing_available = True
                    r.expire(flow_key, 30)

            return JsonResponse(
                dict(
                    nodes=active, segments=visited, is_starting=flow.is_starting(), editing_available=editing_available
                )
            )

    class Simulate(OrgObjPermsMixin, SmartReadView):
        @csrf_exempt
        def dispatch(self, *args, **kwargs):
            return super().dispatch(*args, **kwargs)

        def get(self, request, *args, **kwargs):  # pragma: needs cover
            return HttpResponseRedirect(reverse("flows.flow_editor", args=[self.get_object().uuid]))

        def post(self, request, *args, **kwargs):
            try:
                json_dict = json.loads(request.body)
            except Exception as e:  # pragma: needs cover
                return JsonResponse(dict(status="error", description="Error parsing JSON: %s" % str(e)), status=400)

            if not settings.MAILROOM_URL:  # pragma: no cover
                return JsonResponse(
                    dict(status="error", description="mailroom not configured, cannot simulate"), status=500
                )

            flow = self.get_object()
            client = mailroom.get_client()

            analytics.track(request.user, "temba.flow_simulated", dict(flow=flow.name, uuid=flow.uuid))

            channel_uuid = "440099cf-200c-4d45-a8e7-4a564f4a0e8b"
            channel_name = "Test Channel"

            # build our request body, which includes any assets that mailroom should fake
            payload = {
                "org_id": flow.org_id,
                "assets": {
                    "channels": [
                        {
                            "uuid": channel_uuid,
                            "name": channel_name,
                            "address": "+18005551212",
                            "schemes": ["tel"],
                            "roles": ["send", "receive", "call"],
                            "country": "US",
                        }
                    ]
                },
            }

            if "flow" in json_dict:
                payload["flows"] = [{"uuid": flow.uuid, "definition": json_dict["flow"]}]

            # check if we are triggering a new session
            if "trigger" in json_dict:
                payload["trigger"] = json_dict["trigger"]

                # ivr flows need a connection in their trigger
                if flow.flow_type == Flow.TYPE_VOICE:
                    payload["trigger"]["connection"] = {
                        "channel": {"uuid": channel_uuid, "name": channel_name},
                        "urn": "tel:+12065551212",
                    }

                payload["trigger"]["environment"] = flow.org.as_environment_def()
                payload["trigger"]["user"] = self.request.user.as_engine_ref()

                try:
                    return JsonResponse(client.sim_start(payload))
                except mailroom.MailroomException:
                    return JsonResponse(dict(status="error", description="mailroom error"), status=500)

            # otherwise we are resuming
            elif "resume" in json_dict:
                payload["resume"] = json_dict["resume"]
                payload["resume"]["environment"] = flow.org.as_environment_def()
                payload["session"] = json_dict["session"]

                try:
                    return JsonResponse(client.sim_resume(payload))
                except mailroom.MailroomException:
                    return JsonResponse(dict(status="error", description="mailroom error"), status=500)

    class Broadcast(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):

            query = forms.CharField(
                required=False,
                widget=ContactSearchWidget(attrs={"widget_only": True, "placeholder": _("Enter contact query")}),
            )

            exclude_inactive = forms.BooleanField(
                label=_("Exclude inactive contacts"),
                required=False,
                initial=False,
                help_text=_("Any contacts who have not sent a message in the last 90 days will not be started."),
                widget=CheckboxWidget(),
            )

            exclude_in_other = forms.BooleanField(
                label=_("Exclude contacts currently in a flow"),
                required=False,
                initial=False,
                help_text=_("Any contacts currently in a flow will not be interrupted and not started in this flow."),
                widget=CheckboxWidget(),
            )

            exclude_reruns = forms.BooleanField(
                label=_("Exclude contacts previously in this flow"),
                required=False,
                initial=False,
                help_text=_(
                    "Any contacts who have gone through this flow in the last 90 days will not be started again."
                ),
                widget=CheckboxWidget(),
            )

            def clean_query(self):
                query = self.cleaned_data.get("query")
                exclude_inactive = self.data.get("exclude_inactive")

                if exclude_inactive:
                    now = timezone.now()
                    recency_window = now - timedelta(days=90)
                    query = f"({query}) AND last_seen_on > {self.instance.org.format_datetime(recency_window, show_time=False)}"

                if query:
                    try:
                        parsed = parse_query(self.instance.org, query)
                        query = parsed.query
                    except SearchException as e:
                        raise ValidationError(str(e))

                return query

            def clean(self):
                cleaned_data = super().clean()

                if self.is_valid():
                    query = cleaned_data.get("query")

                    if not query:
                        self.add_error("query", _("This field is required."))

                return cleaned_data

            class Meta:
                model = Flow
                fields = ("query", "exclude_inactive", "exclude_in_other", "exclude_reruns")

        form_class = Form
        success_message = ""
        submit_button_name = _("Start Flow")
        success_url = "uuid@flows.flow_editor"

        blockers = {
            "already_starting": _(
                "This flow is already being started - please wait until that process completes before starting "
                "more contacts."
            ),
            "no_send_channel": _(
                'To get started you need to <a href="%(link)s">add a channel</a> to your workspace which will allow '
                "you to send messages to your contacts."
            ),
            "no_call_channel": _(
                'To get started you need to <a href="%(link)s">add a voice channel</a> to your workspace which will '
                "allow you to make and receive calls."
            ),
        }

        warnings = {
            "facebook_topic": _(
                "This flow does not specify a Facebook topic. You may still start this flow but Facebook contacts who "
                "have not sent an incoming message in the last 24 hours may not receive it."
            ),
            "no_templates": _(
                "This flow does not use message templates. You may still start this flow but WhatsApp contacts who "
                "have not sent an incoming message in the last 24 hours may not receive it."
            ),
        }

        def has_facebook_topic(self, flow):
            if not flow.is_legacy():
                definition = flow.get_current_revision().get_migrated_definition()
                for node in definition.get("nodes", []):
                    for action in node.get("actions", []):
                        if action.get("type", "") == "send_msg" and action.get("topic", ""):
                            return True

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            flow = self.get_object()
            context["warnings"] = self.get_warnings(flow)
            context["blockers"] = self.get_blockers(flow)

            now = timezone.now()
            recency_window = now - timedelta(days=90)
            context["recency_window"] = flow.org.format_datetime(recency_window, show_time=False)
            context["inactive_threshold"] = self.request.branding.get("inactive_threshold", 0)
            return context

        def get_blockers(self, flow) -> list:
            blockers = []

            if flow.org.is_suspended:
                blockers.append(Org.BLOCKER_SUSPENDED)
            elif flow.org.is_flagged:
                blockers.append(Org.BLOCKER_FLAGGED)
            elif flow.is_starting():
                blockers.append(self.blockers["already_starting"])

            if flow.flow_type == Flow.TYPE_MESSAGE and not flow.org.get_send_channel():
                blockers.append(self.blockers["no_send_channel"] % {"link": reverse("channels.channel_claim")})
            elif flow.flow_type == Flow.TYPE_VOICE and not flow.org.get_call_channel():
                blockers.append(self.blockers["no_call_channel"] % {"link": reverse("channels.channel_claim")})

            return blockers

        def get_warnings(self, flow) -> list:
            warnings = []

            # facebook channels need to warn if no topic is set
            facebook_channel = flow.org.get_channel(Channel.ROLE_SEND, scheme=URN.FACEBOOK_SCHEME)
            if facebook_channel and not self.has_facebook_topic(flow):
                warnings.append(self.warnings["facebook_topic"])

            # if we have a whatsapp channel that requires a message template; exclude twilio whatsApp
            whatsapp_channel = flow.org.channels.filter(
                role__contains=Channel.ROLE_SEND, schemes__contains=[URN.WHATSAPP_SCHEME], is_active=True
            ).exclude(channel_type__in=["TWA"])
            if whatsapp_channel:
                # check to see we are using templates
                templates = flow.get_dependencies_metadata("template")
                if not templates:
                    warnings.append(self.warnings["no_templates"])

                # check that this template is synced and ready to go
                for ref in templates:
                    template = flow.org.templates.filter(uuid=ref["uuid"]).first()
                    if not template:
                        warnings.append(
                            _(f"The message template {ref['name']} does not exist on your account and cannot be sent.")
                        )
                    elif not template.is_approved():
                        warnings.append(
                            _(f"Your message template {template.name} is not approved and cannot be sent.")
                        )
            return warnings

        def save(self, *args, **kwargs):
            query = self.form.cleaned_data["query"]
            restart_participants = not self.form.cleaned_data["exclude_reruns"]
            include_contacts_with_runs = not self.form.cleaned_data["exclude_in_other"]

            analytics.track(self.request.user, "temba.flow_broadcast", dict(query=query))

            # queue the flow start to be started by mailroom
            self.object.async_start(
                self.request.user,
                groups=(),
                contacts=(),
                query=query,
                restart_participants=restart_participants,
                include_active=include_contacts_with_runs,
            )
            return self.object

    class Assets(OrgPermsMixin, SmartTemplateView):
        """
        Provides environment and languages to the new editor
        """

        @classmethod
        def derive_url_pattern(cls, path, action):
            return rf"^{path}/{action}/(?P<org>\d+)/(?P<fingerprint>[\w-]+)/(?P<type>environment|language)/((?P<uuid>[a-z0-9-]{{36}})/)?$"

        def derive_org(self):
            if not hasattr(self, "org"):
                self.org = Org.objects.get(id=self.kwargs["org"])
            return self.org

        def get(self, *args, **kwargs):
            org = self.derive_org()
            asset_type_name = kwargs["type"]

            if asset_type_name == "environment":
                return JsonResponse(org.as_environment_def())
            else:
                results = [{"iso": code, "name": languages.get_name(code)} for code in org.flow_languages]
                return JsonResponse({"results": sorted(results, key=lambda lang: lang["name"])})

    class Launch(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        flow_params_fields = []
        flow_params_values = []

        class Form(forms.ModelForm):
            MODE_SELECT = "select"
            MODE_QUERY = "query"
            MODE_CHOICES = (
                (MODE_SELECT, _("Enter contacts and groups to start below")),
                (MODE_QUERY, _("Search for contacts to start")),
            )

            launch_type = forms.ChoiceField(choices=LAUNCH_CHOICES, initial=LAUNCH_IMMEDIATELY, widget=SelectWidget)

            mode = forms.ChoiceField(
                widget=SelectWidget(
                    attrs={"placeholder": _("Select contacts or groups to start in the flow"), "widget_only": True}
                ),
                choices=MODE_CHOICES,
                initial=MODE_SELECT,
            )

            omnibox = OmniboxField(
                required=False,
                widget=OmniboxChoice(
                    attrs={
                        "placeholder": _("Select contact and groups"),
                        "groups": True,
                        "contacts": True,
                        "widget_only": True,
                    }
                ),
            )

            query = forms.CharField(
                required=False,
                widget=ContactSearchWidget(attrs={"widget_only": True, "placeholder": _("Enter contact query")}),
            )

            exclude_in_other = forms.BooleanField(
                label=_("Exclude contacts currently in a flow"),
                required=False,
                initial=False,
                help_text=_("Any contacts currently in a flow will not be interrupted and not started in this flow."),
                widget=CheckboxWidget(),
            )

            exclude_reruns = forms.BooleanField(
                label=_("Exclude contacts previously in this flow"),
                required=False,
                initial=False,
                help_text=_(
                    "Any contacts who have gone through this flow in the last 90 days will not be started again."
                ),
                widget=CheckboxWidget(),
            )

            def clean_omnibox(self):
                omnibox = self.cleaned_data.get("omnibox")
                return omnibox_deserialize(self.instance.org, omnibox) if omnibox else {}

            def clean_query(self):
                query = self.cleaned_data.get("query")
                if query:
                    try:
                        parsed = parse_query(self.instance.org, query)
                        query = parsed.query
                    except SearchException as e:
                        raise ValidationError(str(e))
                return query

            def clean(self):
                cleaned_data = super().clean()
                mode = cleaned_data.get("mode")
                omnibox = cleaned_data.get("omnibox")
                query = cleaned_data.get("query")

                if mode == self.MODE_SELECT and not omnibox:
                    self.add_error("omnibox", _("This field is required."))
                elif mode == self.MODE_QUERY and not query:
                    # TODO https://github.com/nyaruka/temba-components/issues/103
                    # self.add_error("query", _("This field is required."))
                    raise ValidationError(_("Contact query is required."))

                # check whether there are any flow starts that are incomplete
                if self.instance.is_starting():
                    raise ValidationError(
                        _(
                            "This flow is already being started, please wait until that process is complete before "
                            "starting more contacts."
                        )
                    )

                if self.instance.org.is_suspended:
                    raise ValidationError(
                        _(
                            "Sorry, your workspace is currently suspended. "
                            "To enable starting flows, please contact support."
                        )
                    )
                if self.instance.org.is_flagged:
                    raise ValidationError(
                        _(
                            "Sorry, your workspace is currently flagged. To enable starting flows, please contact support."
                        )
                    )

                return cleaned_data

            class Meta:
                model = Flow
                fields = ("launch_type", "mode", "omnibox", "query", "exclude_in_other", "exclude_reruns")

            def __init__(self, *args, **kwargs):
                self.user = kwargs.pop("user")
                self.flow = kwargs.pop("flow")
                FlowCRUDL.Launch.flow_params_fields = []
                FlowCRUDL.Launch.flow_params_values = []

                super().__init__(*args, **kwargs)
                # self.fields["omnibox"].set_user(self.user)

                for counter, flow_param in enumerate(sorted(self.flow.get_trigger_params())):
                    self.fields[f"flow_param_field_{counter}"] = forms.CharField(
                        required=False,
                        initial=flow_param,
                        label=False,
                        widget=forms.TextInput(attrs={"readonly": True, "title": flow_param, "clean": True}),
                    )
                    self.fields[f"flow_param_value_{counter}"] = forms.CharField(
                        required=False, widget=InputWidget(attrs={"widget_only": True})
                    )
                    if f"flow_param_field_{counter}" not in FlowCRUDL.Launch.flow_params_fields:
                        FlowCRUDL.Launch.flow_params_fields.append(f"flow_param_field_{counter}")
                    if f"flow_param_value_{counter}" not in FlowCRUDL.Launch.flow_params_values:
                        FlowCRUDL.Launch.flow_params_values.append(f"flow_param_value_{counter}")

        form_class = Form
        success_message = ""
        submit_button_name = _("OK")
        success_url = "uuid@flows.flow_editor"

        def derive_fields(self):
            return (
                ("launch_type", "mode", "omnibox", "query", "exclude_in_other", "exclude_reruns")
                + tuple(self.flow_params_fields)
                + tuple(self.flow_params_values)
            )

        @staticmethod
        def has_facebook_topic(flow):
            if not flow.is_legacy():
                definition = flow.get_current_revision().get_migrated_definition()
                for node in definition.get("nodes", []):
                    for action in node.get("actions", []):
                        if action.get("type", "") == "send_msg" and action.get("topic", ""):
                            return True

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            flow = self.get_object()
            org = flow.org

            warnings = []

            # facebook channels need to warn if no topic is set
            facebook_channel = org.get_channel(Channel.ROLE_SEND, scheme=URN.FACEBOOK_SCHEME)
            if facebook_channel:
                if not self.has_facebook_topic(flow):
                    warnings.append(
                        _(
                            "This flow does not specify a Facebook topic. You may still start this flow but Facebook contacts who have not sent an incoming message in the last 24 hours may not receive it."
                        )
                    )

            # if we have a whatsapp channel
            whatsapp_channel = org.get_channel(Channel.ROLE_SEND, scheme=URN.WHATSAPP_SCHEME)
            if whatsapp_channel:
                # check to see we are using templates
                templates = flow.get_dependencies_metadata("template")
                if not templates:
                    warnings.append(
                        _(
                            "This flow does not use message templates. You may still start this flow but WhatsApp contacts who have not sent an incoming message in the last 24 hours may not receive it."
                        )
                    )

                # check that this template is synced and ready to go
                for ref in templates:
                    template = Template.objects.filter(org=org, uuid=ref["uuid"]).first()
                    if not template:
                        warnings.append(
                            _(f"The message template {ref['name']} does not exist on your account and cannot be sent.")
                        )
                    elif not template.is_approved():
                        warnings.append(
                            _(f"Your message template {template.name} is not approved and cannot be sent.")
                        )

            run_stats = self.object.get_run_stats()

            context["warnings"] = warnings
            context["run_count"] = run_stats["total"]
            context["complete_count"] = run_stats["completed"]
            flow_params_fields = [
                (self.flow_params_fields[count], self.flow_params_values[count])
                for count in range(len(self.flow_params_values))
            ]
            context["flow_params_fields"] = flow_params_fields
            return context

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            kwargs["flow"] = self.object
            return kwargs

        def save(self, *args, **kwargs):
            form = self.form
            flow = self.object

            # save off our broadcast info
            mode = form.cleaned_data["mode"]
            groups = []
            contacts = []
            contact_query = None

            flow_params = build_flow_parameters(self.request.POST, self.flow_params_fields, self.flow_params_values)

            if mode == "query":
                contact_query = form.cleaned_data["query"]
            else:
                omnibox = form.cleaned_data["omnibox"]
                groups = list(omnibox["groups"])
                contacts = list(omnibox["contacts"])

            analytics.track(
                self.request.user,
                "temba.flow_broadcast",
                dict(contacts=len(contacts), groups=len(groups), query=contact_query),
            )

            # activate all our contacts
            flow.async_start(
                self.request.user,
                groups,
                contacts,
                contact_query,
                include_active=not form.cleaned_data["exclude_in_other"],
                restart_participants=not form.cleaned_data["exclude_reruns"],
                params=flow_params,
            )

            return flow

    class MergeFlows(OrgPermsMixin, SmartTemplateView):
        class MergeFlowsForm(forms.Form):
            flow_name = forms.CharField(max_length=64)

        title = _("Combine Flows")
        form_class = MergeFlowsForm

        def derive_org(self):
            self.org = self.request.user.get_org()
            return self.org

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            return context

        def get(self, request, *args, **kwargs):
            context = self.get_context_data(**kwargs)
            queryset = Flow.objects.filter(org=self.org, is_active=True, is_archived=False, is_system=False)
            source = queryset.filter(uuid=self.request.GET.get("source")).first()
            target = queryset.filter(uuid=self.request.GET.get("target")).first()

            if not all((source, target)):
                raise Http404()

            context["source"] = source.get_definition()
            context["target"] = target.get_definition()

            errors = []
            if {source.version_number, target.version_number}.intersection(set(legacy.VERSIONS)):
                errors.append(_("Legacy flows don't support merging."))

            if source.flow_type != target.flow_type:
                errors.append(_("These flows can't be merged because of different flow types."))

            source_graph = Graph(source.get_definition())
            target_graph = Graph(target.get_definition())
            diff_graph = GraphDifferenceMap(source_graph, target_graph)
            diff_graph.compare_graphs()

            if diff_graph.conflicts:
                errors.append(_("These flows can't be merged because of conflicts."))

            serialized_difference = serialize_difference_graph(diff_graph, dumps=True)
            context["errors"] = errors
            context["merging_map"] = serialized_difference
            context["conflict_solutions"] = diff_graph.get_conflict_solutions()

            if len([node for node in diff_graph.diff_nodes_map.values() if node.parent is None]) > 1:
                context["warnings"] = [
                    _(
                        "Some of the flow steps are not matched and can break the flow logic. "
                        "After merging you will need to check all flow steps and resolve issues "
                        "manually on the flow editor page."
                    )
                ]

            not_unique_results = {
                *source_graph.get_not_unique_result_names(),
                *target_graph.get_not_unique_result_names(),
            }
            if not_unique_results:
                context["prevent_merge"] = True
                context["warnings"] = [
                    _(
                        "Flow fields need to be unique to combine flows. The following duplicate flow fields detected: "
                        "%s. Please rename the flow fields and try again."
                    )
                    % ", ".join(not_unique_results)
                ]

            # to avoid issues when transfer data from source flow we need to check whether
            # our flows is not used by other task, and whether they can be changed asynchronously
            validation_query = {
                "source__in": [source, target],
                "status__in": [MergeFlowsTask.STATUS_ACTIVE, MergeFlowsTask.STATUS_PROCESSING],
            }
            if MergeFlowsTask.objects.filter(**validation_query).exists():
                context["prevent_merge"] = True
                context["warnings"] = [
                    _(
                        "There are other tasks that are trying to combine some of these flows. "
                        "Please wait until those tasks are completed and try to combine the flows again."
                    )
                ]

            return self.render_to_response(context)

        def post(self, request, *args, **kwargs):
            queryset = Flow.objects.filter(org=self.org, is_active=True, is_archived=False, is_system=False)
            source = queryset.filter(uuid=self.request.GET.get("source")).first()
            target = queryset.filter(uuid=self.request.GET.get("target")).first()
            definition = {}

            if not all((source, target)):
                raise Http404()

            # return error message if there are some conflicts
            errors = []
            form = self.form_class(data=request.POST)
            if not form.is_valid():
                errors.extend(
                    [message for val in form.errors.as_data().values() for error in val for message in error]
                )

            if {source.version_number, target.version_number}.intersection(set(legacy.VERSIONS)):
                errors.append(_("Legacy flows don't support merging."))

            if source.flow_type != target.flow_type:
                errors.append(_("These flows can't be merged because of different flow types."))

            difference_map = None
            resolved_conflicts = deserialize_dict_param_from_request("conflicts", request.POST)
            difference_map_data = request.POST.get("merging_map")
            if difference_map_data:
                difference_map = deserialize_difference_graph(difference_map_data, loads=True)
                if difference_map.conflicts and resolved_conflicts:
                    difference_map.apply_conflict_resolving(resolved_conflicts)
            if difference_map is None:
                source_graph = Graph(source.get_definition())
                target_graph = Graph(target.get_definition())
                difference_map = GraphDifferenceMap(source_graph, target_graph)
                difference_map.compare_graphs()

            definition = difference_map.definition

            if difference_map.conflicts:
                errors.append(_("These flows can't be merged because of conflicts."))

            if errors:
                context = self.get_context_data()
                context.update(
                    {
                        "source": source.get_definition(),
                        "target": target.get_definition(),
                        "flow_name": request.POST.get("flow_name"),
                        "merging_map": serialize_difference_graph(difference_map, dumps=True),
                        "conflict_solutions": difference_map.get_conflict_solutions(),
                        "errors": errors,
                    }
                )
                if len([node for node in difference_map.diff_nodes_map.values() if node.parent is None]) > 1:
                    context["warnings"] = [
                        _(
                            "Some of the flow steps are not matched and can break the flow logic. After merging you will need to check all flow steps and resolve issues manually on the flow editor page."
                        )
                    ]
                return self.render_to_response(context)

            if definition:
                merging_metadata = {
                    "origin_node_uuids": {
                        node_uuid: node.uuid for node_uuid, node in difference_map.diff_nodes_origin_map.items()
                    },
                    "origin_exit_uuids": {
                        origin_uuid: new_uuid
                        for node in difference_map.diff_nodes_map.values()
                        for origin_uuid, new_uuid in node.origin_exits_map.items()
                    },
                }
                merging_task = MergeFlowsTask.objects.create(
                    source=source,
                    target=target,
                    merge_name=request.POST.get("flow_name"),
                    definition=definition,
                    merging_metadata=merging_metadata,
                    created_by=self.get_user(),
                    modified_by=self.get_user(),
                )
                merging_task.run()

            messages.info(
                self.request,
                _("Your flows are being combined right now. We will notify you by email when it is complete."),
            )

            return HttpResponseRedirect(reverse("flows.flow_list"))

    class MergingFlowsTable(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        """
        Intercooler helper which renders rows of merging flow tasks to be embedded in an existing table with infinite scrolling
        """

        paginate_by = 50

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            flow = self.get_object()
            merging_tasks = MergeFlowsTask.objects.filter(target=flow).order_by("create_on")

            # paginate
            modified_on = self.request.GET.get("modified_on", None)
            if modified_on:
                uuid = self.request.GET["uuid"]

                modified_on = iso8601.parse_date(modified_on)
                merging_tasks = merging_tasks.filter(modified_on__lte=modified_on).exclude(uuid=uuid)

            # we grab one more than our page to denote whether there's more to get
            merging_tasks = list(merging_tasks.order_by("-modified_on")[: self.paginate_by + 1])
            context["more"] = len(merging_tasks) > self.paginate_by
            merging_tasks = merging_tasks[: self.paginate_by]

            context["merging_tasks"] = merging_tasks
            context["start_date"] = flow.org.get_delete_date(archive_type=Archive.TYPE_FLOWRUN)
            context["paginate_by"] = self.paginate_by
            return context

    class LaunchStudioFlow(ModalMixin, OrgPermsMixin, SmartTemplateView):
        class LaunchStudioFlowForm(forms.Form):
            def __init__(self, *args, **kwargs):
                self.org = kwargs.pop("org")
                flows = kwargs.pop("flows")
                numbers = kwargs.pop("numbers")
                super().__init__(*args, **kwargs)
                self.fields["flow"].choices = flows
                self.fields["channel"].choices = numbers

            flow = forms.ChoiceField(
                widget=SelectWidget(
                    attrs={
                        "placeholder": _("Select Twilio Studio Flow to launch."),
                        "searchable": True,
                    }
                ),
                choices=[],
            )

            omnibox = OmniboxField(
                label=_("Contacts & Groups"),
                required=False,
                help_text=_("These contacts will be added to the flow, sending the first message if appropriate."),
                widget=OmniboxChoice(
                    attrs={
                        "placeholder": _("Select the group"),
                        "groups": True,
                        "contacts": True,
                        "urns": True,
                        "widget_only": True,
                    }
                ),
            )

            channel = forms.ChoiceField(
                widget=SelectWidget(
                    attrs={
                        "placeholder": _("Select the channel that contacts will receive the flow."),
                        "widget_only": True,
                        "searchable": True,
                    }
                ),
                choices=[],
            )

            def clean_omnibox(self):
                starting = self.cleaned_data.get("omnibox")
                if not starting:  # pragma: needs cover
                    raise ValidationError(_("You must specify at least one contact or one group to start a flow."))
                return omnibox_deserialize(self.org, starting)

        permission = "flows.flow_launch"
        success_url = "@contacts.contact_list"
        submit_button_name = _("OK")
        form_class = LaunchStudioFlowForm
        studio_flows = []
        studio_numbers = []
        studio_flow_numbers = []

        def dispatch(self, request, *args, **kwargs):
            org = self.derive_org()
            if not org or not org.get_twilio_client():
                raise Http404

            twilio_client = org.get_twilio_client()
            flows_webhook_prefix = f"https://webhooks.twilio.com/v1/Accounts/{twilio_client.auth[0]}/Flows/"

            def get_flow_sids(_number):
                if _number.sms_application_sid == "" and _number.sms_url.startswith(flows_webhook_prefix):
                    yield _number.sms_url[len(flows_webhook_prefix) :]
                if _number.voice_application_sid == "" and _number.voice_url.startswith(flows_webhook_prefix):
                    yield _number.voice_url[len(flows_webhook_prefix) :]

            if twilio_client:
                studio_flows = twilio_client.studio.flows.stream(page_size=1000)
                flows = [(flow.sid, flow.friendly_name) for flow in studio_flows if flow.status == "published"]
                self.studio_flows = sorted(flows, key=lambda x: x[1])

                numbers = []
                active_numbers = twilio_client.api.incoming_phone_numbers.stream(page_size=1000)
                for number in active_numbers:
                    numbers.append((number.phone_number, number.friendly_name))
                    self.studio_flow_numbers.extend(
                        (flow_sid, number.phone_number) for flow_sid in get_flow_sids(number)
                    )
                self.studio_numbers = sorted(numbers, key=lambda x: x[1])
            return super().dispatch(request, *args, **kwargs)

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            kwargs["flows"] = self.studio_flows
            kwargs["numbers"] = self.studio_numbers
            return kwargs

        def form_valid(self, form):
            start = StudioFlowStart.create(
                org=self.derive_org(),
                user=self.get_user(),
                flow_sid=form.cleaned_data["flow"],
                channel=form.cleaned_data["channel"],
                groups=form.cleaned_data["omnibox"]["groups"],
                contacts=form.cleaned_data["omnibox"]["contacts"],
                urns=form.cleaned_data["omnibox"]["urns"],
            )
            start.async_start()
            messages.info(
                self.request,
                _(
                    "Your Twilio Studio flow was launched but contacts that are still active on it will not receive "
                    "messages again."
                ),
            )
            return super(ModalMixin, self).form_valid(form)

        def get_context_data(self, **kwargs):
            context_data = super(ModalMixin, self).get_context_data(**kwargs)
            context_data["numbers"] = json.dumps(self.studio_numbers)
            context_data["flow_numbers"] = json.dumps(self.studio_flow_numbers)
            return context_data

    class Monitoring(OrgPermsMixin, SmartReadView):
        refresh = 60000
        template_name = "flows/monitoring.haml"
        select_data_sql = """
        SELECT
               fs.flow_id as id,
               min(fs.created_on)                                       as start_time,
               max(fs.modified_on)                                      as updated_time,
               count(ct.id) filter ( where ct.is_active = false )       as invalid_contacts,
               count(fr.id) filter ( where fr.status = 'C')             as reached_contacts,
               count(fr.id) filter ( where fr.responded = true )        as bounces,
               max(case when fr.status in ('S', 'P') then 1 else 0 end) as has_running,
               count(ct.id) filter ( where ct.status = 'S' )            as opt_outs,
               count(fr.id) filter ( where fr.status = 'F')             as carrier_errors,
               count(fr.id) filter ( where fr.status in ('E', 'I'))     as ccl_errors,
               sum((
                   SELECT count(*) filter ( where evt->>'type' = 'msg_received' )
                   FROM jsonb_array_elements(fr.events) evt)
               ) as inbound
        FROM flows_flowstart as fs
        LEFT JOIN flows_flowrun as fr on fs.id = fr.start_id
        LEFT JOIN contacts_contact as ct on fr.contact_id = ct.id
        WHERE fs.flow_id=%s
        GROUP BY fs.flow_id;
        """

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["current_time"] = timezone.now()

            flow = self.get_object()
            data = Flow.objects.using("readonly").raw(self.select_data_sql, params=[flow.id])
            agg_starts = flow.starts.using("readonly").aggregate(total_contacts=Sum("contact_count"))
            total_contacts = agg_starts.get("total_contacts") or 0
            try:
                processed_contacts = data[0].reached_contacts + data[0].ccl_errors + data[0].carrier_errors
                context["start_time"] = data[0].start_time
                context["updated_time"] = data[0].updated_time
                context["end_time"] = data[0].updated_time if not data[0].has_running else None
                context["total_contacts"] = total_contacts
                context["reached_contacts"] = data[0].reached_contacts
                context["remaining_contacts"] = total_contacts - processed_contacts
                context["invalid_contacts"] = data[0].invalid_contacts
                context["opt_outs"] = data[0].opt_outs
                context["inbound"] = data[0].inbound
                context["bounces"] = data[0].bounces
                context["ccl_errors"] = data[0].ccl_errors
                context["carrier_errors"] = data[0].carrier_errors
            except IndexError:
                context.update(
                    {
                        "start_time": "-",
                        "updated_time": "-",
                        "end_time": None,
                        "total_contacts": 0,
                        "invalid_contacts": 0,
                        "reached_contacts": 0,
                        "remaining_contacts": 0,
                        "bounces": 0,
                        "inbound": 0,
                        "opt_outs": 0,
                        "ccl_errors": 0,
                        "carrier_errors": 0,
                    }
                )
            return context


# this is just for adhoc testing of the preprocess url
class PreprocessTest(FormView):  # pragma: no cover
    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return HttpResponse(
            json.dumps(dict(text="Norbert", extra=dict(occupation="hoopster", skillz=7.9))),
            content_type="application/json",
        )


class FlowLabelForm(forms.ModelForm):
    name = forms.CharField(required=True, widget=InputWidget(), label=_("Name"))
    parent = forms.ModelChoiceField(
        FlowLabel.objects.all(),
        required=False,
        label=_("Parent"),
        widget=SelectWidget(attrs={"placeholder": _("Select label")}),
        help_text=_("Optional parent label which can be used to group related labels."),
    )
    flows = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        label = None
        if "label" in kwargs:
            label = kwargs["label"]
            del kwargs["label"]

        super().__init__(*args, **kwargs)
        qs = FlowLabel.objects.filter(org=self.org, parent=None)

        if label:
            qs = qs.exclude(id=label.pk)

        self.fields["parent"].queryset = qs

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if FlowLabel.objects.filter(org=self.org, name=name).exclude(pk=self.instance.id).exists():
            raise ValidationError(_("Name already used"))
        return name

    class Meta:
        model = FlowLabel
        fields = "__all__"


class FlowLabelCRUDL(SmartCRUDL):
    model = FlowLabel
    actions = ("create", "update", "delete")

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ("uuid",)
        redirect_url = "@flows.flow_list"
        cancel_url = "@flows.flow_list"
        success_message = ""
        submit_button_name = _("Delete")

        def get_success_url(self):
            return reverse("flows.flow_list")

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()
            self.object.delete()
            return self.render_modal_response()

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = FlowLabelForm
        success_url = "id@flows.flow_filter"
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            kwargs["label"] = self.get_object()
            return kwargs

        def derive_fields(self):
            if FlowLabel.objects.filter(parent=self.get_object()):  # pragma: needs cover
                return ("name",)
            else:
                return ("name", "parent")

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ("name", "parent", "flows")
        success_url = "hide"
        form_class = FlowLabelForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def pre_save(self, obj, *args, **kwargs):
            obj = super().pre_save(obj, *args, **kwargs)
            obj.org = self.request.user.get_org()
            return obj

        def post_save(self, obj, *args, **kwargs):
            obj = super().post_save(obj, *args, **kwargs)

            flow_ids = []
            if self.form.cleaned_data["flows"]:  # pragma: needs cover
                flow_ids = [int(f) for f in self.form.cleaned_data["flows"].split(",") if f.isdigit()]

            flows = Flow.objects.filter(org=obj.org, is_active=True, pk__in=flow_ids)

            if flows:  # pragma: needs cover
                obj.toggle_label(flows, add=True)

            return obj


class FlowStartCRUDL(SmartCRUDL):
    model = FlowStart
    actions = ("list",)

    class List(OrgFilterMixin, OrgPermsMixin, SmartListView):
        title = _("Flow Start Log")
        ordering = ("-created_on",)
        select_related = ("flow", "created_by")
        paginate_by = 25

        def get_gear_links(self):
            return [dict(title=_("Flows"), style="button-light", href=reverse("flows.flow_list"))]

        def derive_queryset(self, *args, **kwargs):
            qs = super().derive_queryset(*args, **kwargs)

            if self.request.GET.get("type") == "manual":
                qs = qs.filter(start_type=FlowStart.TYPE_MANUAL)
            else:
                qs = qs.filter(start_type__in=(FlowStart.TYPE_MANUAL, FlowStart.TYPE_API, FlowStart.TYPE_API_ZAPIER))

            return qs.prefetch_related("contacts", "groups")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)

            filtered = False
            if self.request.GET.get("type") == "manual":
                context["url_params"] = "?type=manual&"
                filtered = True

            context["filtered"] = filtered

            FlowStartCount.bulk_annotate(context["object_list"])

            return context


class FlowTemplateForm(forms.ModelForm):
    tags = forms.CharField(required=False)
    orgs = forms.ModelMultipleChoiceField(
        Org.objects.filter(is_active=True, is_suspended=False),
        required=False,
        widget=SelectMultipleWidget(attrs={"searchable": True, "placeholder": "Select Org"}),
        help_text="Select organizations that can use this template",
    )
    tags_text = forms.CharField(
        required=False,
        label=_("Tags"),
        help_text=_("Keywords to make it easy to locate related items that have the same tag"),
        widget=SelectWidget(
            attrs={
                "widget_only": False,
                "multi": True,
                "searchable": True,
                "tags": True,
                "space_select": False,
                "placeholder": _("Select keywords classify the template"),
            }
        ),
    )
    description = forms.CharField(
        max_length=200,
        label=_("Description"),
        required=False,
        widget=CompletionTextarea(attrs={"placeholder": _("Enter description here")}),
    )
    group_text = forms.ChoiceField(
        required=True,
        label=_("Template Group"),
        widget=SelectWidget(
            attrs={
                "widget_only": False,
                "searchable": False,
                "placeholder": _("Select a group from the list"),
            }
        ),
    )
    file = forms.FileField(validators=[FileExtensionValidator(allowed_extensions=("json",))])

    global_view = forms.BooleanField(required=False, initial=False, widget=CheckboxWidget(attrs={"widget_only": True}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["group_text"].choices = list(
            FlowTemplateGroup.objects.values_list("uuid", "name").order_by("name")
        )

    class Meta:
        model = FlowTemplate
        fields = ("name", "tags", "orgs", "global_view", "file", "description", "group_text")
        widgets = {"name": InputWidget()}

    @classmethod
    def read_uploaded_file(cls, uploaded_file):
        if uploaded_file:
            return json.loads(uploaded_file.read())
        return dict()

    @classmethod
    def ignore_choices_error(cls, cleaned_data, errors):
        ignore = False
        error = errors.get("group_text")
        if error and "Select a valid choice" in error[0]:
            ignore = True
        return ignore

    def clean(self):
        cleaned = super().clean()

        uploaded = cleaned.get("file")
        json_data = self.read_uploaded_file(uploaded)
        flows = json_data.get("flows", [])

        if uploaded and len(flows) != 1:
            error_instance = forms.ValidationError(_(f"json file should contain only one file, {len(flows)} found"))
            self.add_error("file", error_instance)

        # only continue if base validation passed
        if not self.is_valid():
            return cleaned

        cleaned["document"] = json_data
        cleaned["group_text"] = self.data.get("group_text")
        return cleaned


class FlowTemplateGroupBaseForm(forms.ModelForm):
    class Meta:
        model = FlowTemplateGroup
        fields = ("name",)
        widgets = {"name": InputWidget()}


class FlowTemplateCRUDL(SmartCRUDL):
    model = FlowTemplate
    actions = (
        "list",
        "create",
        "update",
        "delete",
        "filter",
        "create_flow",
        "create_group",
        "update_group",
        "delete_group",
        "create_from_flow",
    )

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = FlowTemplateForm
        success_url = "@flows.flowtemplate_list"
        success_message = ""

        def save(self, obj):
            user = self.request.user
            group_text = self.form.cleaned_data["group_text"]
            name = self.form.cleaned_data["name"]
            orgs = self.form.cleaned_data["orgs"]
            global_view = self.form.cleaned_data["global_view"]
            tags = self.form.cleaned_data["tags_text"].strip("[]").replace("'", "").replace(" ", "").split(",")
            document = self.form.cleaned_data["document"]
            description = self.form.cleaned_data["description"]
            group = FlowTemplateGroup.get_or_create_obj(group_text)
            if obj.document:
                document = obj.document

            name = FlowTemplate.get_unique_name(name)
            self.object = FlowTemplate.objects.create(
                name=name,
                group=group,
                global_view=global_view,
                document=document,
                tags=tags,
                description=description,
                created_by=user,
            )

            self.object.orgs.add(*orgs)

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class UpdateForm(FlowTemplateForm):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.fields["tags_text"].initial = self.instance.tags
                self.fields["group_text"].initial = self.instance.group.uuid

        form_class = UpdateForm
        template_name = "flows/flowtemplate_create.haml"
        exclude = ("file", "document")
        success_message = ""

        def get_object_org(self):
            return self.get_user().get_org()

        def pre_save(self, obj):
            group_text = self.form.cleaned_data["group_text"]
            obj.tags = self.form.cleaned_data["tags_text"].strip("[]").replace("'", "").replace(" ", "").split(",")
            obj.group = FlowTemplateGroup.get_or_create_obj(group_text)
            return obj

    class List(OrgFilterMixin, OrgPermsMixin, SmartListView):
        title = _("Flow Templates")
        ordering = ("-created_on",)
        select_related = ("group", "created_by")
        fields = ["name", "tags", "description", "group", "created_on", "created_by"]
        paginate_by = 25

        def derive_queryset(self, *args, **kwargs):
            queryset = super(SmartListView, self).get_queryset(**kwargs)
            search_query = self.request.GET.get("search")

            if search_query:
                queryset = queryset.filter(name__icontains=search_query)
            return queryset

        def get_groups(self, **kwargs):
            return FlowTemplateGroup.get_group_count()

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["groups"] = self.get_groups()
            return context

    class Filter(List):
        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["gear_links"] = self.get_gear_links()
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<group>[^/]+)/$" % (path, action)

        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(**kwargs)
            filter_value = self.kwargs.get("group")

            if filter_value:
                queryset = queryset.filter(group__uuid=filter_value)
            return queryset

        def get_group(self):
            return FlowTemplateGroup.objects.get(uuid=self.kwargs.get("group"))

        def derive_title(self, *args, **kwargs):
            return self.get_group().name

        def get_gear_links(self):
            group = self.get_group()

            return [
                dict(
                    id="update-group",
                    title=_("Edit Group"),
                    href=reverse("flows.flowtemplate_update_group", args=[group.pk]),
                    modax=_("Edit Group"),
                ),
                dict(
                    id="delete-group",
                    title=_("Delete Group"),
                    href=reverse("flows.flowtemplate_delete_group", args=[group.pk]),
                    modax=_("Delete Group"),
                    cancel_name="Cancel",
                ),
            ]

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ("id",)
        cancel_url = "@flows.flowtemplate_list"
        success_url = "@flows.flowtemplate_list"
        success_message = ""
        submit_button_name = _("Delete")

        def get_object_org(self):
            return self.get_user().get_org()

        def post(self, request, *args, **kwargs):
            instance = FlowTemplate.objects.get(pk=kwargs.get("pk"))
            instance.delete()
            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response

    class CreateFromFlow(Create):
        exclude = ("file",)

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<flow>[^/]+)/$" % (path, action)

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["ignore_upload"] = True
            return context

        def derive_initial(self):
            flow = Flow.objects.get(pk=self.kwargs["flow"])
            return {"name": flow.name}

        def pre_save(self, obj):
            obj.document = FlowTemplate.get_flow_dict(self.kwargs.get("flow"), self.request)
            return obj

    class CreateFlow(OrgPermsMixin, SmartUpdateView):
        slug_url_kwarg = "uuid"

        def post(self, request, *args, **kwargs):
            user = request.user
            org = user.get_org()
            template_name = self.request.POST.get("name")
            template_uuid = kwargs.get("uuid")
            response_info = dict()
            status = 200

            if template_name:
                try:
                    data = FlowTemplate.objects.get(uuid=template_uuid)
                    document = data.document
                    exported_flow = document["flows"][0]
                    flow_template_name = template_name
                    flow_template_name = Flow.get_unique_name(org, flow_template_name)
                    exported_flow["name"] = flow_template_name
                    document["flows"] = [exported_flow]

                    org.import_app(document, user)
                    created_flow = Flow.objects.filter(name=flow_template_name, org=org, is_active=True).first()
                    response_info["name"] = flow_template_name
                    if created_flow:
                        response_info["flow_url"] = reverse("flows.flow_editor", args=[created_flow.uuid])
                except Exception as e:
                    response_info["error"] = str(e)
                    status = 500
            else:
                response_info["error"] = "Kindly provide a template name"
                status = 400

            return JsonResponse(response_info, status=status)

    class CreateGroup(ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = FlowTemplateGroupBaseForm
        success_url = "@flows.flowtemplate_list"
        success_message = ""

        def save(self, obj):
            name = FlowTemplateGroup.get_unique_name(obj.name)
            FlowTemplateGroup.objects.create(name=name)

    class UpdateGroup(ModalMixin, OrgPermsMixin, SmartUpdateView):
        def get_object(self, *args, **kwargs):
            return FlowTemplateGroup.objects.get(pk=self.kwargs.get("pk"))

        form_class = FlowTemplateGroupBaseForm
        success_url = "uuid@flows.flowtemplate_filter"
        success_message = ""

    class DeleteGroup(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ("name",)
        cancel_url = "uuid@flows.flowtemplate_filter"
        success_url = "@flows.flowtemplate_list"
        success_message = ""
        submit_button_name = _("Delete")
        template_name = "flows/flowtemplate_delete.haml"

        def get_object_org(self):
            return self.get_user().get_org()

        def get_object(self, *args, **kwargs):
            return FlowTemplateGroup.objects.get(pk=self.kwargs.get("pk"))

        def post(self, request, *args, **kwargs):
            pk = self.kwargs.get("pk")

            instance = FlowTemplateGroup.objects.get(pk=pk)
            instance.delete()
            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response
