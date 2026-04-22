import logging
from datetime import date, timedelta
from urllib.parse import quote_plus

from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartFormView,
    SmartListView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db.models import Case, Count, IntegerField, Max, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.db.models.functions.text import Upper
from django.forms import Form
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _

from temba.api.v2.serializers import MsgReadSerializer
from temba.archives.models import Archive
from temba.channels.models import Channel
from temba.contacts.models import URN, Contact, ContactGroup, ContactURN
from temba.contacts.search.omnibox import omnibox_deserialize, omnibox_query, omnibox_results_to_dict
from temba.formax import FormaxMixin
from temba.mailroom import MailroomException, get_client
from temba.notifications.views import NotificationTargetMixin
from temba.orgs.models import Org
from temba.orgs.views import (
    DependencyDeleteModal,
    DependencyUsagesModal,
    MenuMixin,
    ModalMixin,
    OrgObjPermsMixin,
    OrgPermsMixin,
)
from temba.utils import analytics, json, on_transaction_commit
from temba.utils.fields import (
    CheckboxWidget,
    CompletionTextarea,
    InputWidget,
    JSONField,
    OmniboxChoice,
    OmniboxField,
    SelectMultipleWidget,
    SelectWidget,
    TembaChoiceField,
)
from temba.utils.models import patch_queryset_count
from temba.utils.views import BulkActionMixin, ComponentFormMixin, SpaMixin

from .models import (
    Broadcast,
    Conversation,
    ConversationOwner,
    ConversationTemplate,
    ExportMessagesTask,
    Label,
    LabelCount,
    Msg,
    Schedule,
    SystemLabel,
)
from .tasks import export_messages_task


class SendMessageForm(Form):

    omnibox = OmniboxField(
        label=_("Recipients"),
        required=False,
        help_text=_("The contacts to send the message to"),
        widget=OmniboxChoice(
            attrs={
                "placeholder": _("Recipients, enter contacts or groups"),
                "widget_only": True,
                "groups": True,
                "contacts": True,
                "urns": True,
            }
        ),
    )

    text = forms.CharField(
        widget=CompletionTextarea(
            attrs={
                "placeholder": _("Hi @contact.name!"),
                "widget_only": True,
                "counter": "temba-charcount",
                "spellchecker": False,
            }
        )
    )

    schedule = forms.BooleanField(
        widget=CheckboxWidget(attrs={"widget_only": True}),
        required=False,
        label=_("Schedule for later"),
        help_text=None,
    )
    step_node = forms.CharField(widget=forms.HiddenInput, max_length=36, required=False)

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org
        self.fields["omnibox"].default_country = org.default_country_code

    def clean(self):
        cleaned = super().clean()

        if self.is_valid():
            omnibox = cleaned.get("omnibox")
            step_node = cleaned.get("step_node")

            if not step_node and not omnibox:
                self.add_error("omnibox", _("At least one recipient is required."))

        return cleaned


class InboxView(SpaMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
    """
    Base class for inbox views with message folders and labels listed by the side
    """

    refresh = 10000
    add_button = True
    system_label = None
    fields = ("from", "message", "received")
    search_fields = ("text__icontains", "contact__name__icontains", "contact__urns__path__icontains")
    paginate_by = 100
    default_order = ("-created_on", "-id")
    allow_export = False
    show_channel_logs = False
    bulk_actions = ()
    bulk_action_permissions = {"resend": "msgs.broadcast_send", "delete": "msgs.msg_update"}

    def derive_label(self):
        return self.system_label

    def derive_export_url(self):
        redirect = quote_plus(self.request.get_full_path())
        label = self.derive_label()
        label_id = label.uuid if isinstance(label, Label) else label
        return "%s?l=%s&redirect=%s" % (reverse("msgs.msg_export"), label_id, redirect)

    def pre_process(self, request, *args, **kwargs):
        if self.system_label:
            org = request.user.get_org()
            self.queryset = SystemLabel.get_queryset(org, self.system_label)

    def get_queryset(self, **kwargs):
        qs = super().get_queryset(**kwargs)

        # if we are searching, limit to last 90, and enforce distinct since we'll be joining on multiple tables
        if "search" in self.request.GET:
            last_90 = timezone.now() - timedelta(days=90)

            # we need to find get the field names we're ordering on without direction
            distinct_on = (f.lstrip("-") for f in self.derive_ordering())

            qs = qs.filter(created_on__gte=last_90).distinct(*distinct_on)

        if self.show_channel_logs:
            qs = qs.prefetch_related("channel_logs")

        return qs

    def get_bulk_action_labels(self):
        return self.get_user().get_org().msgs_labels.all()

    def get_context_data(self, **kwargs):
        org = self.request.user.get_org()
        counts = SystemLabel.get_counts(org)

        label = self.derive_label()

        # if there isn't a search filtering the queryset, we can replace the count function with a pre-calculated value
        if "search" not in self.request.GET:
            if isinstance(label, Label) and not label.is_folder():
                patch_queryset_count(self.object_list, label.get_visible_count)
            elif isinstance(label, str):
                patch_queryset_count(self.object_list, lambda: counts[label])

        context = super().get_context_data(**kwargs)
        folders = [
            dict(count=counts[SystemLabel.TYPE_INBOX], label=_("Inbox"), url=reverse("msgs.msg_inbox")),
            dict(count=counts[SystemLabel.TYPE_FLOWS], label=_("Text Flows"), url=reverse("msgs.msg_flow")),
            dict(
                count=counts[SystemLabel.TYPE_FLOW_VOICE], label=_("Voice Flows"), url=reverse("msgs.msg_flow_voice")
            ),
            dict(count=counts[SystemLabel.TYPE_ARCHIVED], label=_("Archived"), url=reverse("msgs.msg_archived")),
            dict(count=counts[SystemLabel.TYPE_OUTBOX], label=_("Outbox"), url=reverse("msgs.msg_outbox")),
            dict(count=counts[SystemLabel.TYPE_SENT], label=_("Text Sent"), url=reverse("msgs.msg_sent")),
            dict(count=counts[SystemLabel.TYPE_SENT_VOICE], label=_("Voice Sent"), url=reverse("msgs.msg_sent_voice")),
            dict(
                count=counts[SystemLabel.TYPE_CALLS],
                label=_("Missed Calls"),
                url=reverse("channels.channelevent_calls"),
            ),
            dict(
                count=counts[SystemLabel.TYPE_SCHEDULED],
                label=_("Schedules"),
                url=reverse("msgs.broadcast_schedule_list"),
            ),
            dict(count=counts[SystemLabel.TYPE_FAILED], label=_("Failed"), url=reverse("msgs.msg_failed")),
        ]

        context["org"] = org
        context["folders"] = folders
        context["labels"] = Label.get_hierarchy(org)
        context["has_messages"] = (
            any(counts.values()) or Archive.objects.filter(org=org, archive_type=Archive.TYPE_MSG).exists()
        )
        context["current_label"] = label
        context["export_url"] = self.derive_export_url()
        context["show_channel_logs"] = self.show_channel_logs
        context["start_date"] = org.get_delete_date(archive_type=Archive.TYPE_MSG)

        # if refresh was passed in, increase it by our normal refresh time
        previous_refresh = self.request.GET.get("refresh")
        if previous_refresh:
            context["refresh"] = int(previous_refresh) + self.derive_refresh()

        return context

    def get_gear_links(self):
        links = []
        if self.allow_export and self.has_org_perm("msgs.msg_export"):
            links.append(
                dict(
                    id="export-messages",
                    title=_("Download"),
                    href=self.derive_export_url(),
                    modax=_("Download Messages"),
                )
            )
        return links


class BroadcastForm(forms.ModelForm):
    message = forms.CharField(
        required=True,
        widget=CompletionTextarea(attrs={"placeholder": _("Hi @contact.name!"), "spellchecker": False}),
        max_length=Broadcast.MAX_TEXT_LEN,
    )

    omnibox = JSONField(
        label=_("Recipients"),
        required=False,
        help_text=_("The contacts to send the message to"),
        widget=OmniboxChoice(
            attrs={
                "placeholder": _("Recipients, enter contacts or groups"),
                "groups": True,
                "contacts": True,
                "urns": True,
            }
        ),
    )

    def is_valid(self):
        valid = super().is_valid()
        if valid:
            if "omnibox" not in self.data or len(self.data["omnibox"].strip()) == 0:  # pragma: needs cover
                self.errors["__all__"] = self.error_class([_("At least one recipient is required")])
                return False

        return valid

    class Meta:
        model = Broadcast
        fields = "__all__"


class BroadcastCRUDL(SmartCRUDL):
    actions = ("send", "update", "schedule_read", "schedule_list")
    model = Broadcast

    class ScheduleRead(SpaMixin, FormaxMixin, OrgObjPermsMixin, SmartReadView):
        title = _("Schedule Message")

        def derive_title(self):
            return _("Scheduled Message")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["object_list"] = self.get_object().children.all()
            return context

        def derive_formax_sections(self, formax, context):
            if self.has_org_perm("msgs.broadcast_update"):
                formax.add_section(
                    "contact", reverse("msgs.broadcast_update", args=[self.object.pk]), icon="icon-megaphone"
                )

            if self.has_org_perm("schedules.schedule_update"):
                formax.add_section(
                    "schedule",
                    reverse("schedules.schedule_update", args=[self.object.schedule.pk]),
                    icon="icon-calendar",
                    action="formax",
                )

    class Update(OrgObjPermsMixin, ComponentFormMixin, SmartUpdateView):
        form_class = BroadcastForm
        fields = ("message", "omnibox")
        field_config = {"restrict": {"label": ""}, "omnibox": {"label": ""}, "message": {"label": "", "help": ""}}
        success_message = ""
        success_url = "msgs.broadcast_schedule_list"

        def derive_initial(self):
            org = self.object.org
            results = [*self.object.groups.all(), *self.object.contacts.all()]
            selected = omnibox_results_to_dict(org, results, version="2")
            message = self.object.text[self.object.base_language]
            return dict(message=message, omnibox=selected)

        def save(self, *args, **kwargs):
            form = self.form
            broadcast = self.object
            org = broadcast.org

            # save off our broadcast info
            omnibox = omnibox_deserialize(org, self.form.cleaned_data["omnibox"])

            # set our new message
            broadcast.text = {broadcast.base_language: form.cleaned_data["message"]}
            broadcast.update_recipients(groups=omnibox["groups"], contacts=omnibox["contacts"], urns=omnibox["urns"])

            broadcast.save()
            return broadcast

    class ScheduleList(InboxView):
        refresh = 30000
        title = _("Scheduled Messages")
        fields = ("contacts", "msgs", "sent", "status")
        search_fields = ("text__icontains", "contacts__urns__path__icontains")
        template_name = "msgs/broadcast_schedule_list.haml"
        system_label = SystemLabel.TYPE_SCHEDULED

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("org", "schedule")

    class Send(OrgPermsMixin, ModalMixin, SmartFormView):
        title = _("Send Message")
        form_class = SendMessageForm
        fields = ("omnibox", "text", "schedule", "step_node")
        success_url = "@msgs.msg_inbox"
        submit_button_name = _("Send")

        blockers = {
            "no_send_channel": _(
                'To get started you need to <a href="%(link)s">add a channel</a> to your workspace which will allow '
                "you to send messages to your contacts."
            ),
        }

        def derive_initial(self):
            initial = super().derive_initial()
            org = self.request.user.get_org()

            urn_ids = [_ for _ in self.request.GET.get("u", "").split(",") if _]
            msg_ids = [_ for _ in self.request.GET.get("m", "").split(",") if _]
            contact_uuids = [_ for _ in self.request.GET.get("c", "").split(",") if _]

            if msg_ids or contact_uuids or urn_ids:
                params = {}
                if len(msg_ids) > 0:
                    params["m"] = ",".join(msg_ids)
                if len(contact_uuids) > 0:
                    params["c"] = ",".join(contact_uuids)
                if len(urn_ids) > 0:
                    params["u"] = ",".join(urn_ids)

                results = omnibox_query(org, **params)
                initial["omnibox"] = omnibox_results_to_dict(org, results, version="2")

            initial["step_node"] = self.request.GET.get("step_node", None)
            return initial

        def derive_fields(self):
            if self.request.GET.get("step_node"):
                return ("text", "step_node")
            else:
                return super().derive_fields()

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["blockers"] = self.get_blockers(self.request.org)
            context["recipient_count"] = int(self.request.GET.get("count", 0))
            return context

        def get_blockers(self, org) -> list:
            blockers = []

            if org.is_suspended:
                blockers.append(Org.BLOCKER_SUSPENDED)
            elif org.is_flagged:
                blockers.append(Org.BLOCKER_FLAGGED)
            if not org.get_send_channel():
                blockers.append(self.blockers["no_send_channel"] % {"link": reverse("channels.channel_claim")})

            return blockers

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            step_uuid = form.cleaned_data.get("step_node", None)
            text = form.cleaned_data["text"]
            has_schedule = False

            if step_uuid:
                from .tasks import send_to_flow_node

                get_params = {k: v for k, v in self.request.GET.items()}
                get_params.update({"s": step_uuid})
                send_to_flow_node.delay(org.pk, user.pk, text, **get_params)
            else:

                omnibox = omnibox_deserialize(org, form.cleaned_data["omnibox"])
                has_schedule = form.cleaned_data["schedule"]

                groups = list(omnibox["groups"])
                contacts = list(omnibox["contacts"])
                urns = list(omnibox["urns"])

                schedule = Schedule.create_blank_schedule(org, user) if has_schedule else None
                broadcast = Broadcast.create(
                    org,
                    user,
                    text,
                    groups=groups,
                    contacts=contacts,
                    urns=urns,
                    schedule=schedule,
                    status=Msg.STATUS_QUEUED,
                    template_state=Broadcast.TEMPLATE_STATE_UNEVALUATED,
                )

                if not has_schedule:
                    self.post_save(broadcast)
                    super().form_valid(form)

                analytics.track(
                    self.request.user,
                    "temba.broadcast_created",
                    dict(contacts=len(contacts), groups=len(groups), urns=len(urns)),
                )

            if "HTTP_X_PJAX" in self.request.META:
                success_url = "hide"
                if has_schedule:
                    success_url = reverse("msgs.broadcast_schedule_read", args=[broadcast.id])

                response = self.render_to_response(self.get_context_data(success_url=success_url))
                response["Temba-Success"] = success_url
                return response

            return HttpResponseRedirect(self.get_success_url())

        def post_save(self, obj):
            on_transaction_commit(lambda: obj.send_async())
            return obj


class TestMessageForm(forms.Form):
    channel = TembaChoiceField(Channel.objects.filter(id__lt=0), help_text=_("Which channel will deliver the message"))
    urn = forms.CharField(max_length=14, help_text=_("The URN of the contact delivering this message"))
    text = forms.CharField(max_length=160, widget=forms.Textarea, help_text=_("The message that is being delivered"))

    def __init__(self, *args, **kwargs):  # pragma: needs cover
        org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)
        self.fields["channel"].queryset = Channel.objects.filter(org=org, is_active=True)


class ExportForm(Form):
    LABEL_CHOICES = ((0, _("Just this label")), (1, _("All messages")))

    SYSTEM_LABEL_CHOICES = ((0, _("Just this folder")), (1, _("All messages")))

    export_all = forms.ChoiceField(
        choices=(), label=_("Selection"), initial=0, widget=SelectWidget(attrs={"widget_only": True})
    )

    start_date = forms.DateField(
        required=False,
        help_text=_("Leave blank for the oldest message"),
        widget=InputWidget(attrs={"datepicker": True, "hide_label": True, "placeholder": _("Start Date")}),
    )

    end_date = forms.DateField(
        required=False,
        help_text=_("Leave blank for the latest message"),
        widget=InputWidget(attrs={"datepicker": True, "hide_label": True, "placeholder": _("End Date")}),
    )

    groups = forms.ModelMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(),
        required=False,
        label=_("Groups"),
        widget=SelectMultipleWidget(
            attrs={"widget_only": True, "placeholder": _("Optional: Choose groups to show in your export")}
        ),
    )

    def __init__(self, user, label, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        self.fields["export_all"].choices = self.LABEL_CHOICES if label else self.SYSTEM_LABEL_CHOICES

        self.fields["groups"].queryset = ContactGroup.user_groups.filter(org=self.user.get_org(), is_active=True)
        self.fields["groups"].help_text = _(
            "Export only messages from these contact groups. " "(Leave blank to export all messages)."
        )

    def clean(self):
        cleaned_data = super().clean()
        start_date = cleaned_data.get("start_date")
        end_date = cleaned_data.get("end_date")

        if start_date and start_date > date.today():  # pragma: needs cover
            raise forms.ValidationError(_("Start date can't be in the future."))

        if end_date and start_date and end_date < start_date:  # pragma: needs cover
            raise forms.ValidationError(_("End date can't be before start date"))

        return cleaned_data


class MsgCRUDL(SmartCRUDL):
    model = Msg
    actions = (
        "inbox",
        "flow",
        "archived",
        "menu",
        "outbox",
        "sent",
        "failed",
        "filter",
        "export",
        "flow_voice",
        "sent_voice",
    )

    class Menu(MenuMixin, OrgPermsMixin, SmartTemplateView):  # pragma: no cover
        def derive_menu(self):
            org = self.request.user.get_org()
            counts = SystemLabel.get_counts(org)

            if self.request.GET.get("labels"):
                labels = (
                    Label.all_objects.filter(org=org, is_active=True)
                    .exclude(label_type=Label.TYPE_FOLDER)
                    .order_by(Upper("name"))
                )
                label_counts = LabelCount.get_totals([lb for lb in labels])

                menu = []
                for label in labels:
                    menu.append(
                        self.create_menu_item(
                            menu_id=label.uuid,
                            name=label.name,
                            href=reverse("msgs.msg_filter", args=[label.uuid]),
                            count=label_counts[label],
                        )
                    )
                return menu
            else:
                label_count = Label.label_objects.filter(org=org, is_active=True).count()

                return [
                    self.create_menu_item(
                        name=_("Inbox"),
                        href=reverse("msgs.msg_inbox"),
                        count=counts[SystemLabel.TYPE_INBOX],
                        icon="inbox",
                    ),
                    self.create_menu_item(
                        name=_("Scheduled"),
                        href=reverse("msgs.broadcast_schedule_list"),
                        count=counts[SystemLabel.TYPE_SCHEDULED],
                        icon="clock",
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        name=_("Labels"),
                        endpoint=f"{reverse('msgs.msg_menu')}?labels=1",
                        count=label_count,
                        icon="tag",
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        name=_("Outbox"),
                        href=reverse("msgs.msg_outbox"),
                        count=counts[SystemLabel.TYPE_OUTBOX],
                    ),
                    self.create_menu_item(
                        name=_("Sent"),
                        href=reverse("msgs.msg_sent"),
                        count=counts[SystemLabel.TYPE_SENT],
                    ),
                    self.create_menu_item(
                        name=_("Failed"),
                        href=reverse("msgs.msg_failed"),
                        count=counts[SystemLabel.TYPE_FAILED],
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        name=_("Flows"),
                        href=reverse("msgs.msg_flow"),
                        count=counts[SystemLabel.TYPE_FLOWS],
                    ),
                    self.create_menu_item(
                        name=_("Calls"),
                        href=reverse("channels.channelevent_calls"),
                        count=counts[SystemLabel.TYPE_CALLS],
                    ),
                    self.create_divider(),
                    self.create_menu_item(
                        name=_("Archived"),
                        href=reverse("msgs.msg_archived"),
                        count=counts[SystemLabel.TYPE_ARCHIVED],
                        icon="archive",
                    ),
                ]

    class Export(ModalMixin, OrgPermsMixin, SmartFormView):

        form_class = ExportForm
        submit_button_name = "Export"
        success_url = "@msgs.msg_inbox"

        def derive_label(self):
            # label is either a UUID of a Label instance (36 chars) or a system label type code (1 char)
            label_id = self.request.GET["l"]
            if len(label_id) == 1:
                return label_id, None
            else:
                return None, Label.all_objects.get(org=self.request.user.get_org(), uuid=label_id)

        def get_success_url(self):
            redirect = self.request.GET.get("redirect")
            if redirect and not url_has_allowed_host_and_scheme(redirect, self.request.get_host()):
                redirect = None

            return redirect or reverse("msgs.msg_inbox")

        def form_invalid(self, form):  # pragma: needs cover
            if "_format" in self.request.GET and self.request.GET["_format"] == "json":
                return HttpResponse(
                    json.dumps(dict(status="error", errors=form.errors)), content_type="application/json", status=400
                )
            else:
                return super().form_invalid(form)

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            export_all = bool(int(form.cleaned_data["export_all"]))
            groups = form.cleaned_data["groups"]
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]

            system_label, label = (None, None) if export_all else self.derive_label()

            # is there already an export taking place?
            existing = ExportMessagesTask.get_recent_unfinished(org)
            if existing:
                messages.info(
                    self.request,
                    _(
                        "There is already an export in progress, started by %s. You must wait "
                        "for that export to complete before starting another." % existing.created_by.username
                    ),
                )

            # otherwise, off we go
            else:
                export = ExportMessagesTask.create(
                    org,
                    user,
                    system_label=system_label,
                    label=label,
                    groups=groups,
                    start_date=start_date,
                    end_date=end_date,
                )

                on_transaction_commit(lambda: export_messages_task.delay(export.id))

                if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):  # pragma: needs cover
                    messages.info(
                        self.request,
                        _("We are preparing your export. We will e-mail you at %s when " "it is ready.")
                        % self.request.user.username,
                    )

                else:
                    dl_url = reverse("assets.download", kwargs=dict(type="message_export", pk=export.pk))
                    messages.info(
                        self.request,
                        _("Export complete, you can find it here: %s (production users " "will get an email)")
                        % dl_url,
                    )

            messages.success(self.request, self.derive_success_message())

            if "HTTP_X_PJAX" not in self.request.META:
                return HttpResponseRedirect(self.get_success_url())
            else:  # pragma: no cover
                response = self.render_modal_response(form)
                response["REDIRECT"] = self.get_success_url()
                return response

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            kwargs["label"] = self.derive_label()[1]
            return kwargs

    class Inbox(InboxView):
        title = _("Inbox")
        template_name = "msgs/message_box.haml"
        system_label = SystemLabel.TYPE_INBOX
        bulk_actions = ("archive", "label")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact")

    class Flow(InboxView):
        title = _("Flow Messages")
        template_name = "msgs/message_box.haml"
        system_label = SystemLabel.TYPE_FLOWS
        bulk_actions = ("archive", "label")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact")

    class FlowVoice(InboxView):
        title = _("Flow Voice Messages")
        template_name = "msgs/message_box.haml"
        system_label = SystemLabel.TYPE_FLOW_VOICE
        bulk_actions = ("label",)
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact")

    class Archived(InboxView):
        title = _("Archived")
        template_name = "msgs/msg_archived.haml"
        system_label = SystemLabel.TYPE_ARCHIVED
        bulk_actions = ("restore", "label", "delete")
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("labels").select_related("contact")

    class Outbox(InboxView):
        title = _("Outbox Messages")
        template_name = "msgs/msg_outbox.haml"
        system_label = SystemLabel.TYPE_OUTBOX
        bulk_actions = ()
        allow_export = True
        show_channel_logs = True

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            # stuff in any pending broadcasts
            context["pending_broadcasts"] = (
                Broadcast.objects.filter(
                    org=self.request.user.get_org(),
                    status__in=[Msg.STATUS_QUEUED, Msg.STATUS_INITIALIZING],
                    schedule=None,
                )
                .select_related("org")
                .prefetch_related("groups", "contacts", "urns")
                .order_by("-created_on")
            )
            return context

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact")

    class Sent(InboxView):
        title = _("Sent Messages")
        template_name = "msgs/msg_sent.haml"
        system_label = SystemLabel.TYPE_SENT
        bulk_actions = ()
        allow_export = True
        show_channel_logs = True
        default_order = ("-sent_on", "-id")

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact")

    class SentVoice(InboxView):
        title = _("Sent Voice Messages")
        template_name = "msgs/msg_sent.haml"
        system_label = SystemLabel.TYPE_SENT_VOICE
        bulk_actions = ()
        allow_export = True
        show_channel_logs = True

        def get_queryset(self, **kwargs):  # pragma: needs cover
            qs = super().get_queryset(**kwargs)
            return qs.prefetch_related("channel_logs").select_related("contact")

    class Failed(InboxView):
        title = _("Failed Outgoing Messages")
        template_name = "msgs/msg_failed.haml"
        success_message = ""
        system_label = SystemLabel.TYPE_FAILED
        allow_export = True
        show_channel_logs = True

        def get_bulk_actions(self):
            return () if self.request.org.is_suspended else ("resend",)

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).select_related("contact")

    class Filter(InboxView):
        template_name = "msgs/msg_filter.haml"
        bulk_actions = ("label",)
        contact_uuids = []

        def get_filter_contact_uuids(self):
            msgs = self.get_queryset()
            return [msg.contact.uuid for msg in msgs]

        def derive_title(self, *args, **kwargs):
            return self.derive_label().name

        def get_gear_links(self):
            links = []

            label = self.derive_label()
            if self.has_org_perm("msgs.msg_update"):
                if label.is_folder():
                    links.append(
                        dict(
                            id="update-label",
                            title=_("Edit Folder"),
                            href=reverse("msgs.label_update", args=[label.pk]),
                            modax=_("Edit Folder"),
                        )
                    )
                else:
                    links.append(
                        dict(
                            id="update-label",
                            title=_("Edit Label"),
                            href=reverse("msgs.label_update", args=[label.pk]),
                            modax=_("Edit Label"),
                        )
                    )

            if self.has_org_perm("msgs.msg_export"):
                links.append(
                    dict(
                        id="export-messages",
                        title=_("Download"),
                        href=self.derive_export_url(),
                        modax=_("Download Messages"),
                    )
                )

            if self.has_org_perm("msgs.broadcast_send"):
                links.append(
                    dict(
                        id="send-all",
                        title=_("Send All"),
                        href=f"{reverse('msgs.broadcast_send')}?c={','.join(self.contact_uuids)}",
                        modax=_("Send Message"),
                    )
                )

            links.append(
                dict(
                    id="label-usages",
                    title=_("Usages"),
                    modax=_("Usages"),
                    href=reverse("msgs.label_usages", args=[label.uuid]),
                )
            )

            if label.is_folder():
                if self.has_org_perm("msgs.label_delete_folder"):
                    links.append(
                        dict(
                            id="delete-folder",
                            title=_("Delete Folder"),
                            href=reverse("msgs.label_delete_folder", args=[label.id]),
                            modax=_("Delete Folder"),
                        )
                    )
            else:
                if self.has_org_perm("msgs.label_delete"):
                    links.append(
                        dict(
                            id="delete-label",
                            title=_("Delete Label"),
                            href=reverse("msgs.label_delete", args=[label.uuid]),
                            modax=_("Delete Label"),
                        )
                    )

            return links

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<label>[^/]+)/$" % (path, action)

        def derive_label(self):
            try:
                return self.request.user.get_org().msgs_labels.get(uuid=self.kwargs["label"])
            except Label.DoesNotExist:
                raise Http404

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            qs = self.derive_label().filter_messages(qs).filter(visibility=Msg.VISIBILITY_VISIBLE)
            qs = qs.prefetch_related("labels").select_related("contact")

            # Adding contact uuids to be used on send all functionality
            self.contact_uuids = [msg.contact.uuid for msg in qs]

            return qs


class BaseLabelForm(forms.ModelForm):
    def clean_name(self):
        name = self.cleaned_data["name"]

        if not Label.is_valid_name(name):
            raise forms.ValidationError(_("Name must not be blank or begin with punctuation"))

        existing_id = self.existing.pk if self.existing else None
        if Label.all_objects.filter(org=self.org, name__iexact=name, is_active=True).exclude(pk=existing_id).exists():
            raise forms.ValidationError(_("Name must be unique"))

        count = Label.label_objects.filter(org=self.org, is_active=True).count()
        if count >= self.org.get_limit(Org.LIMIT_LABELS):
            raise forms.ValidationError(
                _(
                    "This workspace has %d labels and the limit is %s. You must delete existing ones before you can "
                    "create new ones." % (count, self.org.get_limit(Org.LIMIT_LABELS))
                )
            )

        return name

    class Meta:
        model = Label
        fields = ("name",)
        labels = {"name": _("Name")}
        widgets = {"name": InputWidget()}


class LabelForm(BaseLabelForm):
    folder = forms.ModelChoiceField(
        Label.folder_objects.none(),
        required=False,
        label=_("Folder"),
        widget=SelectWidget(attrs={"placeholder": _("Select folder")}),
        help_text=_("Optional folder which can be used to group related labels."),
        empty_label=" --------- ",
    )

    messages = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        self.org = kwargs.pop("org")
        self.existing = kwargs.pop("object", None)

        super().__init__(*args, **kwargs)

        self.fields["folder"].queryset = Label.folder_objects.filter(org=self.org, is_active=True)

    class Meta(BaseLabelForm.Meta):
        fields = ("name", "folder")


class FolderForm(BaseLabelForm):
    def __init__(self, *args, **kwargs):
        self.org = kwargs.pop("org")
        self.existing = kwargs.pop("object", None)

        super().__init__(*args, **kwargs)


class LabelCRUDL(SmartCRUDL):
    model = Label
    actions = ("create", "create_folder", "update", "usages", "delete", "delete_folder", "list")

    class List(OrgPermsMixin, SmartListView):
        paginate_by = None
        default_order = ("name",)

        def derive_queryset(self, **kwargs):
            return Label.label_objects.filter(org=self.request.user.get_org())

        def render_to_response(self, context, **response_kwargs):
            results = [{"id": lb.uuid, "text": lb.name} for lb in context["object_list"]]
            return HttpResponse(json.dumps(results), content_type="application/json")

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ("name", "folder", "messages")
        success_url = "hide"
        form_class = LabelForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def save(self, obj):
            user = self.request.user
            self.object = Label.get_or_create(user.get_org(), user, obj.name, obj.folder)

        def post_save(self, obj, *args, **kwargs):
            obj = super().post_save(obj, *args, **kwargs)
            if self.form.cleaned_data["messages"]:  # pragma: needs cover
                msg_ids = [int(m) for m in self.form.cleaned_data["messages"].split(",") if m.isdigit()]
                messages = Msg.objects.filter(org=obj.org, pk__in=msg_ids)
                if messages:
                    obj.toggle_label(messages, add=True)

            return obj

    class CreateFolder(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ("name",)
        success_url = "@msgs.msg_inbox"
        form_class = FolderForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def save(self, obj):
            user = self.request.user
            self.object = Label.get_or_create_folder(user.get_org(), user, obj.name)

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        success_url = "uuid@msgs.msg_filter"
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            kwargs["object"] = self.get_object()
            return kwargs

        def get_form_class(self):
            return FolderForm if self.get_object().is_folder() else LabelForm

        def derive_title(self):
            return _("Update Folder") if self.get_object().is_folder() else _("Update Label")

        def derive_fields(self):
            return ("name",) if self.get_object().is_folder() else ("name", "folder")

    class Usages(DependencyUsagesModal):
        permission = "msgs.label_read"

    class Delete(DependencyDeleteModal):
        cancel_url = "@msgs.msg_inbox"
        success_url = "@msgs.msg_inbox"
        success_message = _("Your label has been deleted.")

    class DeleteFolder(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        success_url = "@msgs.msg_inbox"
        redirect_url = "@msgs.msg_inbox"
        cancel_url = "@msgs.msg_inbox"
        success_message = _("Your label folder has been deleted.")
        fields = ("uuid",)
        submit_button_name = _("Delete")

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()

            # don't actually release if a label has been added
            if self.object.has_child_labels():
                return self.render_to_response(self.get_context_data())

            self.object.release(self.request.user)
            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response


class ConversationTemplateForm(forms.ModelForm):
    name = forms.CharField(
        widget=InputWidget(),
    )
    text = forms.CharField(
        widget=CompletionTextarea(
            attrs={
                "label": "Text",
                "placeholder": _("Hi @contact.name!"),
                "widget_only": True,
                "counter": "temba-charcount",
                "spellchecker": False,
            }
        )
    )

    def __init__(self, *args, **kwargs):
        self.org = kwargs.pop("org")
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        if not self.org:
            return cleaned

        has_name_conflicts = self.org.conversation_templates.filter(name=cleaned["name"]).exists()
        if self.instance and cleaned["name"] == self.instance.name:
            has_name_conflicts = False

        if has_name_conflicts:
            self.add_error("name", _("The template with this name already exists."))

        return cleaned

    class Meta:
        model = ConversationTemplate
        fields = ("name", "text")
        labels = {"name": _("Name"), "text": _("Text")}


class ConversationCRUDL(SmartCRUDL):
    model = Conversation
    actions = (
        "list",
        "start",
        "create_template",
        "update_template",
        "delete_template",
        "preview_template",
        "archive",
        "send_attachment",
        "unread",
    )

    class Start(OrgPermsMixin, ModalMixin, SmartFormView):
        class StartConversationForm(Form):
            omnibox = OmniboxField(
                label=_("Recipients"),
                required=False,
                help_text=_("The contacts to send the message to"),
                widget=OmniboxChoice(
                    attrs={
                        "placeholder": _("Recipients, enter contacts or groups"),
                        "widget_only": True,
                        "groups": True,
                        "contacts": True,
                        "urns": True,
                    }
                ),
            )

            template = forms.ModelChoiceField(
                queryset=ConversationTemplate.objects.none(),
                widget=SelectWidget(),
            )

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.org = org
                self.fields["omnibox"].default_country = org.default_country_code
                self.fields["template"].queryset = ConversationTemplate.objects.filter(org=self.org)

            def clean(self):
                cleaned = super().clean()
                if self.is_valid():
                    omnibox = cleaned.get("omnibox")
                    if not omnibox:
                        self.add_error("omnibox", _("At least one recipient is required."))
                return cleaned

        title = _("Send Message")
        form_class = StartConversationForm
        fields = ("omnibox", "template")
        success_url = "@msgs.conversation_list"
        submit_button_name = _("Send")

        blockers = {
            "no_send_channel": _(
                'To get started you need to <a href="%(link)s">add a channel</a> to your workspace which will allow '
                "you to send messages to your contacts."
            ),
        }

        def derive_initial(self):
            initial = super().derive_initial()
            org = self.request.user.get_org()

            urns = [_ for _ in self.request.GET.get("u", "").split(",") if _]
            msg_ids = [_ for _ in self.request.GET.get("m", "").split(",") if _]
            contact_uuids = [_ for _ in self.request.GET.get("c", "").split(",") if _]

            if msg_ids or contact_uuids or urns:
                params = {}
                if len(msg_ids) > 0:
                    params["m"] = ",".join(msg_ids)
                if len(contact_uuids) > 0:
                    params["c"] = ",".join(contact_uuids)
                if len(urns) > 0:
                    params["u"] = ",".join(urns)

                results = omnibox_query(org, **params)
                initial["omnibox"] = omnibox_results_to_dict(org, results, version="2")
            return initial

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["blockers"] = self.get_blockers(self.request.org)
            context["recipient_count"] = int(self.request.GET.get("count", 0))
            return context

        def get_blockers(self, org) -> list:
            blockers = []

            if org.is_suspended:
                blockers.append(Org.BLOCKER_SUSPENDED)
            elif org.is_flagged:
                blockers.append(Org.BLOCKER_FLAGGED)
            if not org.get_send_channel():
                blockers.append(self.blockers["no_send_channel"] % {"link": reverse("channels.channel_claim")})

            return blockers

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            omnibox = omnibox_deserialize(org, form.cleaned_data["omnibox"])
            template = form.cleaned_data["template"]
            text = template.text

            groups = list(omnibox["groups"])
            contacts = list(omnibox["contacts"])
            urn_strings = list(omnibox["urns"])

            for urn_as_string in urn_strings:
                urn_as_string = URN.normalize(urn_as_string, org.default_country_code)
                try:
                    urn = ContactURN.objects.filter(org=org, identity=urn_as_string).first()
                    if not urn:
                        contact = Contact.create(org, user, "", "", [urn_as_string], {}, [])
                        urn = contact.urns.first()
                    contacts.append(urn.contact)
                except MailroomException as e:
                    logging.info(f"Unable to create contact: {str(e)}")
                    pass

            broadcast = Broadcast.create(
                org,
                user,
                text,
                groups=groups,
                contacts=contacts,
                urns=[],
                schedule=None,
                status=Msg.STATUS_QUEUED,
                template_state=Broadcast.TEMPLATE_STATE_UNEVALUATED,
            )
            first_contact = self.start_conversations(
                org,
                user,
                groups=groups,
                contacts=contacts,
            )

            self.post_save(broadcast)
            super().form_valid(form)

            analytics.track(
                self.request.user, "temba.broadcast_created", dict(contacts=len(contacts), groups=len(groups))
            )

            if "HTTP_X_PJAX" in self.request.META:
                success_url = reverse("msgs.conversation_list")
                success_url = f"{success_url}?contactUUID={first_contact.uuid}" if first_contact else success_url
                response = self.render_to_response(self.get_context_data(success_url=success_url))
                response["Temba-Success"] = success_url
                return response

            return HttpResponseRedirect(self.get_success_url())

        def start_conversations(self, org, user, groups, contacts) -> Contact:
            contact_ids = [contact.id for contact in contacts]
            contact_ids += Contact.objects.filter(org=org, all_groups__in=groups).values_list("id", flat=True)
            contact_ids = list(set(contact_ids))
            contacts = Contact.objects.filter(org=org, id__in=contact_ids, conversations__isnull=True)

            # create conversations for the contacts without it
            Conversation.objects.bulk_create(
                [Conversation(org=org, contact=contact) for contact in contacts], ignore_conflicts=True
            )

            # update owners for existing ones
            conversations_without_owner = Conversation.objects.filter(org=org, contact_id__in=contact_ids).exclude(
                owners=self.request.user
            )
            for conversation in conversations_without_owner:
                conversation.owners.add(user, through_defaults={"last_read": None})

            # update status to ACTIVE
            Conversation.objects.filter(org=org, contact_id__in=contact_ids, status=Conversation.ARCHIVED).update(
                status=Conversation.ACTIVE
            )
            return Contact.objects.filter(org=org, id__in=contact_ids).first()

        @staticmethod
        def post_save(obj):
            on_transaction_commit(lambda: obj.send_async())
            return obj

    class List(SpaMixin, OrgPermsMixin, NotificationTargetMixin, SmartListView):
        paginate_by = None
        default_order = (
            "-modified_on",
            "contact__name",
        )

        def derive_queryset(self, **kwargs):
            return Conversation.objects.filter(
                org=self.request.user.get_org(), contact__status=Contact.STATUS_ACTIVE, contact__is_active=True
            )

        def get_context_data(self, **kwargs):
            search = self.request.GET.get("search", "")
            archived = str(self.request.GET.get("archived", "false")).lower() == "true"
            context = super().get_context_data(**kwargs)
            context["chats_count"] = self.derive_queryset().filter(status=Conversation.ACTIVE).count()
            context["archived_count"] = self.derive_queryset().filter(status=Conversation.ARCHIVED).count()
            context["templates"] = ConversationTemplate.objects.filter(org=self.request.user.get_org())
            queryset = self.derive_queryset().filter(status=Conversation.ARCHIVED if archived else Conversation.ACTIVE)
            if search:
                search = (
                    str(search).replace(" ", "").replace(",", "").replace("(", "").replace(")", "").replace("-", "")
                )
                queryset = queryset.filter(
                    Q(contact__name__icontains=search) | Q(contact__urns__path__icontains=search)
                )

            return_personal = self.request.GET.get("chats", "all") == "my"
            if return_personal:
                queryset = queryset.filter(owners=self.request.user)

            last_read_subquery = ConversationOwner.objects.filter(
                conversation=OuterRef("pk"),
                owner=self.request.user,
            ).values("last_read")[:1]
            queryset = queryset.annotate(last_read=Subquery(last_read_subquery))
            queryset = queryset.annotate(
                unread_count=Case(
                    When(
                        last_read__isnull=False,
                        then=Coalesce(
                            Subquery(
                                Msg.objects.filter(
                                    direction=Msg.DIRECTION_IN,
                                    contact=OuterRef("contact"),
                                    created_on__gt=OuterRef("last_read"),
                                )
                                .values("contact")
                                .annotate(count=Count("id"))
                                .values("count")[:1]
                            ),
                            0,
                        ),
                    ),
                    default=Value(0),
                    output_field=IntegerField(),
                ),
                last_created_msg=Max("contact__msgs__created_on"),
            )

            queryset = queryset.order_by("-unread_count", "-last_created_msg", *self.default_order).distinct()
            context["chats"] = queryset
            context["ABLY_API_KEY"] = settings.ABLY_API_KEY
            return context

    class Archive(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        permission = "msgs.conversation_start"
        slug_field = "contact__uuid"
        slug_url_kwarg = "uuid"

        success_url = "@msgs.conversation_list"
        redirect_url = "@msgs.conversation_list"
        cancel_url = "@msgs.conversation_list"
        success_message = _("Your conversation has been archived.")
        fields = ("contact",)

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()
            status_swap = {
                Conversation.ARCHIVED: Conversation.ACTIVE,
                Conversation.ACTIVE: Conversation.ARCHIVED,
            }
            self.object.status = status_swap.get(self.object.status, Conversation.ARCHIVED)
            self.object.save(update_fields=["status"])

            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response

        def get_context_data(self, **kwargs):
            self.object = self.get_object()
            context_data = super().get_context_data(**kwargs)
            context_data["submit_button_name"] = (
                _("Archive") if self.object.status == Conversation.ACTIVE else _("Activate")
            )
            return context_data

    class CreateTemplate(OrgPermsMixin, ModalMixin, SmartFormView):
        model = ConversationTemplate
        success_url = "@msgs.conversation_list"
        form_class = ConversationTemplateForm
        fields = ("name", "text")

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            text = form.cleaned_data["text"]
            name = form.cleaned_data["name"]

            ConversationTemplate.objects.create(
                org=org,
                name=name,
                text=text,
            )

            if "HTTP_X_PJAX" in self.request.META:
                success_url = reverse("msgs.conversation_list")
                response = self.render_to_response(self.get_context_data(success_url=success_url))
                response["Temba-Success"] = success_url
                return response

            return HttpResponseRedirect(self.get_success_url())

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

    class UpdateTemplate(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        model = ConversationTemplate
        form_class = ConversationTemplateForm
        permission = "msgs.conversation_create_template"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

    class DeleteTemplate(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ("id",)
        submit_button_name = _("Delete")
        success_url = "@msgs.conversation_list"
        cancel_url = "@msgs.conversation_list"

        def get_object(self, *args, **kwargs):
            return ConversationTemplate.objects.get(org=self.request.user.get_org(), pk=self.kwargs["pk"])

        def post(self, request, *args, **kwargs):
            self.get_object().delete()
            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response

    class PreviewTemplate(OrgObjPermsMixin, SmartReadView):
        model = ConversationTemplate

        def derive_queryset(self):
            queryset = super().derive_queryset()
            return queryset.filter(org=self.request.user.get_org())

        def render_to_response(self, context, **response_kwargs):
            return HttpResponse(context["object"].text)

    class SendAttachment(OrgObjPermsMixin, SmartUpdateView):
        permission = "msgs.conversation_start"
        slug_field = "contact__uuid"
        slug_url_kwarg = "uuid"

        def post(self, request, *args, **kwargs):
            user = self.request.user
            org = user.get_org()
            contact = self.get_object().contact
            attachment = request.FILES.get("attachment", None)
            if not attachment:
                return JsonResponse({"attachment": ["This field is required."]}, status=400)

            attachment_name = f"attachments/conversations/{contact.uuid}/{attachment.name}"
            attachment_path = default_storage.save(attachment_name, ContentFile(attachment.read()))
            attachment_uri = request.build_absolute_uri(reverse("file_storage", kwargs={"file_path": attachment_path}))
            mailroom_response = get_client().msg_send(
                org.id,
                user.id,
                contact.id,
                "",
                [f"{attachment.content_type}:{attachment_uri}"],
            )
            msg = Msg.objects.filter(id=mailroom_response["id"]).first()
            if not msg:
                return JsonResponse({"attachment": ["Failed to send the attachment."]}, status=400)

            serializer = MsgReadSerializer(
                instance=msg,
                context={
                    "org": org,
                    "user": self.request.user,
                },
            )
            return JsonResponse(serializer.data)

    class Unread(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        permission = "msgs.conversation_start"
        slug_field = "contact__uuid"
        slug_url_kwarg = "uuid"

        success_url = "@msgs.conversation_list"
        redirect_url = "@msgs.conversation_list"
        cancel_url = "@msgs.conversation_list"
        success_message = _("Operation completed successfully.")
        fields = ("contact",)

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()

            # Set last_read to just before the last inbound message so it shows as unread
            last_inbound = (
                Msg.objects.filter(contact=self.object.contact, direction=Msg.DIRECTION_IN)
                .order_by("-created_on")
                .first()
            )
            if last_inbound:
                ConversationOwner.objects.filter(conversation=self.object, owner=request.user).update(
                    last_read=last_inbound.created_on - timedelta(microseconds=1)
                )

            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response

        def get_context_data(self, **kwargs):
            self.object = self.get_object()
            context_data = super().get_context_data(**kwargs)
            context_data["submit_button_name"] = _("Mark unread")
            return context_data
