import logging
import socket

from datetime import timedelta

from django import forms
from django.conf import settings
from django.urls import reverse
from django.contrib import messages
from django.views.generic import RedirectView
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from django.http import JsonResponse, HttpResponseRedirect

from smartmin.views import SmartCRUDL, SmartCreateView, SmartListView, SmartUpdateView, SmartReadView

from temba.utils import analytics, on_transaction_commit
from temba.utils.dates import datetime_to_timestamp, timestamp_to_datetime
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.contacts.models import Contact
from temba.flows.models import Flow

from .models import Link, ExportLinksTask
from .tasks import export_link_task
from ..utils.fields import SelectWidget, InputWidget
from ..utils.views import BulkActionMixin

logger = logging.getLogger(__name__)


class BaseFlowForm(forms.ModelForm):
    class Meta:
        model = Link
        fields = "__all__"


class LinkCRUDL(SmartCRUDL):
    actions = ("list", "read", "history", "archived", "create", "update", "api", "export")

    model = Link

    class OrgQuerysetMixin(object):
        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            if not self.request.user.is_authenticated:  # pragma: needs cover
                return queryset.exclude(pk__gt=0)
            else:
                return queryset.filter(org=self.request.user.get_org())

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        class LinkCreateForm(BaseFlowForm):
            name = forms.CharField(widget=InputWidget)
            related_flow = forms.ModelChoiceField(
                required=False,
                queryset=Flow.objects.none(),
                widget=SelectWidget(attrs={"clearable": True, "placeholder": _("Select related flow")}),
            )

            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.user = user
                self.fields["related_flow"].queryset = Flow.objects.filter(
                    org=self.user.get_org(), is_active=True, is_archived=False, is_system=False
                ).order_by("name")

            class Meta:
                model = Link
                fields = ("name", "related_flow", "destination")
                widgets = {
                    "name": InputWidget,
                    "destination": InputWidget(
                        attrs={"placeholder": "E.g. http://example.com, https://example.com", "type": "url"}
                    ),
                }

        form_class = LinkCreateForm
        success_message = ""
        field_config = dict(name=dict(help=_("Choose a name to describe this link, e.g. Luca Survey Webflow")))
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["has_links"] = Link.objects.filter(org=self.request.user.get_org(), is_active=True).count() > 0
            return context

        def save(self, obj):
            analytics.track(self.request.user, "temba.link_created", dict(name=obj.name))
            org = self.request.user.get_org()
            self.object = Link.create(
                org=org,
                user=self.request.user,
                name=obj.name,
                related_flow=obj.related_flow,
                destination=obj.destination,
            )

        def post_save(self, obj):
            return obj

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        fields = ("name",)

        def derive_title(self):
            return self.object.name

        def get_queryset(self):
            return Link.objects.filter(is_active=True)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["recent_start"] = datetime_to_timestamp(timezone.now() - timedelta(minutes=5))
            return context

        def get_gear_links(self):
            links = []

            if self.has_org_perm("links.link_update"):
                links.append(
                    dict(
                        id="edit-link",
                        title=_("Edit"),
                        style="button-primary",
                        href=f"{reverse('links.link_update', args=[self.object.pk])}",
                        modax=_("Update Link"),
                    )
                )

            if self.has_org_perm("links.link_export"):
                links.append(
                    dict(
                        title=_("Export"),
                        style="button-primary",
                        js_class="posterize",
                        href=reverse("links.link_export", args=(self.object.pk,)),
                    )
                )

            return links

    class History(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get_queryset(self):
            return Link.objects.filter(is_active=True)

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            link = self.get_object()

            link_creation = link.created_on - timedelta(hours=1)

            search = self.request.GET.get("search", None)
            search = search.strip() if search else search

            before = int(self.request.GET.get("before", 0))
            after = int(self.request.GET.get("after", 0))

            recent_only = False
            if not before:
                recent_only = True
                before = timezone.now()
            else:
                before = timestamp_to_datetime(before)

            if not after:
                after = before - timedelta(days=90)
            else:
                after = timestamp_to_datetime(after)

            # keep looking further back until we get at least 20 items
            while True:
                activity = link.get_activity(after, before, search)
                if recent_only or len(activity) >= 20 or after == link_creation:
                    break
                else:
                    after = max(after - timedelta(days=90), link_creation)

            # mark our after as the last item in our list
            from temba.links.models import MAX_HISTORY

            if len(activity) >= MAX_HISTORY:
                after = activity[-1]["time"]

            # check if there are more pages to fetch
            context["has_older"] = False
            if not recent_only and before > link.created_on:
                context["has_older"] = bool(link.get_activity(link_creation, after, search))

            context["recent_only"] = recent_only
            context["before"] = datetime_to_timestamp(after)
            context["after"] = datetime_to_timestamp(max(after - timedelta(days=90), link_creation))
            context["activity"] = activity

            return context

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class LinkUpdateForm(BaseFlowForm):
            related_flow = forms.ModelChoiceField(
                required=False,
                queryset=Flow.objects.none(),
                widget=SelectWidget(attrs={"clearable": True, "placeholder": _("Select related flow")}),
            )

            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.user = user
                self.fields["related_flow"].queryset = Flow.objects.filter(
                    org=self.user.get_org(), is_active=True, is_archived=False, is_system=False
                ).order_by("name")

            class Meta:
                model = Link
                fields = ("name", "related_flow", "destination")
                widgets = {
                    "name": InputWidget,
                    "destination": InputWidget(
                        attrs={"placeholder": "E.g. http://example.com, https://example.com", "type": "url"}
                    ),
                }

        success_message = ""
        success_url = "uuid@links.link_read"
        fields = ("name", "related_flow", "destination")
        form_class = LinkUpdateForm

        def derive_fields(self):
            return [field for field in self.fields]

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.related_flow = self.form.cleaned_data.get("related_flow", None)
            return obj

        def post_save(self, obj):
            obj.update_flows()
            return obj

    class Export(OrgObjPermsMixin, SmartReadView):
        def post(self, request, *args, **kwargs):
            user = request.user
            org = user.get_org()

            redirect = request.GET.get("redirect")

            link = Link.objects.filter(org=org, pk=self.kwargs.get("pk")).first()

            # is there already an export taking place?
            existing = ExportLinksTask.get_recent_unfinished(org)
            if existing:
                messages.info(
                    self.request,
                    _(
                        f"There is already an export in progress, started by {existing.created_by.username}. "
                        f"You must wait for that export to complete before starting another."
                    ),
                )

            # otherwise, off we go
            else:
                previous_export = (
                    ExportLinksTask.objects.filter(org=org, created_by=user).order_by("-modified_on").first()
                )
                if previous_export and previous_export.created_on < timezone.now() - timedelta(
                    hours=24
                ):  # pragma: needs cover
                    analytics.track(self.request.user, "temba.link_exported")

                export = ExportLinksTask.create(org, user, link)

                on_transaction_commit(lambda: export_link_task.delay(export.pk))

                if not getattr(settings, "CELERY_ALWAYS_EAGER", False):  # pragma: no cover
                    messages.info(
                        self.request,
                        _(
                            f"We are preparing your export. We will e-mail you at {self.request.user.username} when it is ready."
                        ),
                    )

                else:
                    dl_url = reverse("assets.download", kwargs=dict(type="link_export", pk=export.pk))
                    messages.info(
                        self.request,
                        _(f"Export complete, you can find it here: {dl_url} (production users will get an email)"),
                    )

            return HttpResponseRedirect(redirect or reverse("links.link_read", args=[link.uuid]))

    class BaseList(BulkActionMixin, OrgQuerysetMixin, OrgPermsMixin, SmartListView):
        title = _("Trackable Links")
        refresh = 10000
        fields = ("name", "modified_on")
        default_template = "links/link_list.html"
        default_order = "-created_on"
        search_fields = ("name__icontains", "destination__icontains")
        bulk_actions = ("archive", "restore")
        bulk_action_permissions = {"archive": "links.link_update", "restore": "links.link_update"}

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org_has_links"] = Link.objects.filter(org=self.request.user.get_org(), is_active=True).count()
            context["folders"] = self.get_folders()
            context["request_url"] = self.request.path
            context["actions"] = self.actions

            return context

        def derive_queryset(self, *args, **kwargs):
            qs = super().derive_queryset(*args, **kwargs)
            return qs.exclude(is_active=False)

        def get_folders(self):
            org = self.request.user.get_org()

            return [
                dict(
                    label="Active",
                    url=reverse("links.link_list"),
                    count=Link.objects.filter(is_active=True, is_archived=False, org=org).count(),
                ),
                dict(
                    label="Archived",
                    url=reverse("links.link_archived"),
                    count=Link.objects.filter(is_active=True, is_archived=True, org=org).count(),
                ),
            ]

    class Archived(BaseList):
        actions = ("restore",)
        default_order = ("-created_on",)

        def derive_queryset(self, *args, **kwargs):
            return super().derive_queryset(*args, **kwargs).filter(is_active=True, is_archived=True)

    class List(BaseList):
        title = _("Trackable Links")
        actions = ("archive",)

        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            queryset = queryset.filter(is_active=True, is_archived=False)
            return queryset

    class Api(OrgQuerysetMixin, OrgPermsMixin, SmartListView):
        def get(self, request, *args, **kwargs):
            org = self.request.user.get_org()
            links = Link.objects.filter(is_active=True, is_archived=False, org=org).order_by("name")
            results = [item.as_select2() for item in links]
            return JsonResponse(dict(results=results))


class LinkHandler(RedirectView):
    def get_redirect_url(self, *args, **kwargs):
        from user_agents import parse
        from .tasks import handle_link_task

        link = Link.objects.filter(uuid=self.kwargs.get("uuid")).only("id", "destination").first()
        contact = Contact.objects.filter(uuid=self.request.GET.get("contact")).only("id").first()
        destination_full_url = self.request.GET.get("full_link")
        related_flow_uuid = self.request.GET.get("flow")

        # Whether the contact is from the simulator
        if not contact:
            return destination_full_url or link.destination

        elif link and contact:
            x_forwarded_for = self.request.META.get("HTTP_X_FORWARDED_FOR")
            ip = x_forwarded_for.split(",")[0] if x_forwarded_for else self.request.META.get("REMOTE_ADDR")

            ua_string = self.request.META.get("HTTP_USER_AGENT")
            user_agent = parse(ua_string)

            try:
                host = socket.gethostbyaddr(ip)[0]
                is_host_checking = "amazonaws" in host
            except Exception:
                is_host_checking = False

            if not is_host_checking and not user_agent.is_bot:
                on_transaction_commit(lambda: handle_link_task.delay(link.id, contact.id, related_flow_uuid))

            return destination_full_url or link.destination
        else:
            return None
