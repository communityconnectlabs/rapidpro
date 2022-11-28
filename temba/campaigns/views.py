from django.utils import timezone
from smartmin.views import SmartCreateView, SmartCRUDL, SmartDeleteView, SmartListView, SmartReadView, SmartUpdateView

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import ContactField, ContactGroup
from temba.flows.models import Flow
from temba.msgs.models import Msg
from temba.orgs.views import ModalMixin, OrgFilterMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils import languages, build_flow_parameters, flow_params_context
from temba.utils.fields import CompletionTextarea, InputWidget, SelectWidget, TembaChoiceField
from temba.utils.views import BulkActionMixin

from .models import Campaign, CampaignEvent


class CampaignForm(forms.ModelForm):
    group = TembaChoiceField(
        queryset=ContactGroup.user_groups.none(),
        empty_label=None,
        widget=SelectWidget(attrs={"placeholder": _("Select group"), "searchable": True}),
        label=_("Group"),
        help_text=_("Only contacts in this group will be included in this campaign's events."),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["group"].queryset = ContactGroup.get_user_groups(user.get_org(), ready_only=False)

    class Meta:
        model = Campaign
        fields = ("name", "group")
        labels = {"name": _("Name")}
        widgets = {"name": InputWidget()}


class CampaignCRUDL(SmartCRUDL):
    model = Campaign
    actions = ("create", "read", "update", "list", "archived", "archive", "activate", "monitoring")

    class Update(OrgObjPermsMixin, ModalMixin, SmartUpdateView):
        fields = ("name", "group")
        success_message = ""
        form_class = CampaignForm

        def pre_process(self, request, *args, **kwargs):
            campaign_id = kwargs.get("pk")
            if campaign_id:
                campaign = Campaign.objects.filter(id=campaign_id, is_active=True, is_archived=False)

                if not campaign.exists():
                    raise Http404("Campaign not found")

        def get_success_url(self):
            return reverse("campaigns.campaign_read", args=[self.object.pk])

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["user"] = self.request.user
            return form_kwargs

        def form_valid(self, form):
            previous_group = self.get_object().group
            new_group = form.cleaned_data["group"]

            # save our campaign
            self.object = form.save(commit=False)
            self.save(self.object)

            # if our group changed, create our new fires
            if new_group != previous_group:
                self.object.recreate_events()
                self.object.schedule_events_async()

            return self.render_modal_response(form)

    class Read(OrgObjPermsMixin, SmartReadView):
        def derive_title(self):
            return self.object.name

        def get_gear_links(self):
            links = []

            if self.object.is_archived:
                if self.has_org_perm("orgs.org_export"):
                    links.append(
                        dict(
                            title=_("Export"),
                            href=f"{reverse('orgs.org_export')}?campaign={self.object.id}&archived=1",
                        )
                    )

                if self.has_org_perm("campaigns.campaign_activate"):
                    links.append(
                        dict(
                            title="Activate",
                            js_class="posterize activate-campaign",
                            href=reverse("campaigns.campaign_activate", args=[self.object.id]),
                        )
                    )
            else:
                if self.has_org_perm("campaigns.campaignevent_create"):
                    links.append(
                        dict(
                            id="event-add",
                            title=_("Add Event"),
                            style="button-primary",
                            href=f"{reverse('campaigns.campaignevent_create')}?campaign={self.object.pk}",
                            modax=_("Add Event"),
                        )
                    )
                if self.has_org_perm("orgs.org_export"):
                    links.append(
                        dict(title=_("Export"), href=f"{reverse('orgs.org_export')}?campaign={self.object.id}")
                    )

                if self.has_org_perm("campaigns.campaign_update"):
                    links.append(
                        dict(
                            id="campaign-update",
                            title=_("Edit"),
                            href=reverse("campaigns.campaign_update", args=[self.object.pk]),
                            modax=_("Update Campaign"),
                        )
                    )

                if self.has_org_perm("campaigns.campaign_archive"):
                    links.append(
                        dict(
                            title="Archive",
                            js_class="posterize archive-campaign",
                            href=reverse("campaigns.campaign_archive", args=[self.object.id]),
                        )
                    )

                if self.has_org_perm("flows.flow_monitoring"):
                    links.append(
                        dict(
                            id="Monitoring",
                            title=_("Monitoring"),
                            href=reverse("campaigns.campaign_monitoring", args=[self.object.pk]),
                        )
                    )

            user = self.get_user()
            if user.is_superuser or user.is_staff:
                links.append(
                    dict(
                        title=_("Service"),
                        posterize=True,
                        href=f'{reverse("orgs.org_service")}?organization={self.object.org_id}&redirect_url={reverse("campaigns.campaign_read", args=[self.object.id])}',
                    )
                )

            return links

    class Create(OrgPermsMixin, ModalMixin, SmartCreateView):
        fields = ("name", "group")
        form_class = CampaignForm
        success_message = ""
        success_url = "id@campaigns.campaign_read"

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.org = self.request.user.get_org()
            return obj

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

    class BaseList(OrgFilterMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
        fields = ("name", "group")
        default_template = "campaigns/campaign_list.html"
        default_order = ("-modified_on",)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org_has_campaigns"] = Campaign.objects.filter(org=self.request.user.get_org()).count()
            context["folders"] = self.get_folders()
            context["request_url"] = self.request.path
            return context

        def get_folders(self):
            org = self.request.user.get_org()
            folders = []
            folders.append(
                dict(
                    label="Active",
                    url=reverse("campaigns.campaign_list"),
                    count=Campaign.objects.filter(is_active=True, is_archived=False, org=org).count(),
                )
            )
            folders.append(
                dict(
                    label="Archived",
                    url=reverse("campaigns.campaign_archived"),
                    count=Campaign.objects.filter(is_active=True, is_archived=True, org=org).count(),
                )
            )
            return folders

    class List(BaseList):
        fields = ("name", "group")
        bulk_actions = ("archive",)
        search_fields = ("name__icontains", "group__name__icontains")

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = qs.filter(is_active=True, is_archived=False)
            return qs

    class Archived(BaseList):
        fields = ("name",)
        bulk_actions = ("restore",)
        search_fields = ("name__icontains", "group__name__icontains")

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = qs.filter(is_active=True, is_archived=True)
            return qs

    class Archive(OrgFilterMixin, OrgPermsMixin, SmartUpdateView):

        fields = ()
        success_url = "id@campaigns.campaign_read"
        success_message = _("Campaign archived")

        def save(self, obj):
            obj.apply_action_archive(self.request.user, Campaign.objects.filter(id=obj.id))
            return obj

    class Activate(OrgFilterMixin, OrgPermsMixin, SmartUpdateView):
        fields = ()
        success_url = "id@campaigns.campaign_read"
        success_message = _("Campaign activated")

        def save(self, obj):
            obj.apply_action_restore(self.request.user, Campaign.objects.filter(id=obj.id))
            return obj

    class Monitoring(OrgPermsMixin, SmartReadView):
        template_name = "flows/monitoring.haml"
        permission = "flows.flow_monitoring"
        select_data_sql = """
        SELECT id,
          min(cast(t.flow_data ->> 'start_time' as timestamp))     as start_time,
          max(cast(t.flow_data ->> 'updated_time' as timestamp))   as updated_time,
          sum(cast(t.flow_data ->> 'total_contacts' as integer))   as total_contacts,
          sum(cast(t.flow_data ->> 'bounces' as integer))          as bounces,
          sum(cast(t.flow_data ->> 'reached_contacts' as integer)) as reached_contacts,
          sum(cast(t.flow_data ->> 'inbound' as integer))          as inbound,
          max(cast(t.flow_data ->> 'has_running' as integer))      as has_running
        FROM (
          SELECT cp.id id, ce.flow_id, (
            SELECT row_to_json(flow_data)
            FROM (
              SELECT min(fs.created_on)                                               as start_time,
                     max(fs.modified_on)                                              as updated_time,
                     sum(fs.contact_count)                                            as total_contacts,
                     count(fr.id) filter ( where fr.responded = true )                as bounces,
                     count(fr.id) filter ( where fr.status not in ('A', 'W'))         as reached_contacts,
                     count(fr_evt.type) filter ( where fr_evt.type = 'msg_received' ) as inbound,
                     max(case when fr.status in ('S', 'P') then 1 else 0 end)         as has_running
              FROM flows_flowstart as fs
              LEFT JOIN flows_flowrun as fr on fs.id = fr.start_id
              LEFT JOIN (
                SELECT ffr.id, jsonb_array_elements(ffr.events) ->> 'type' as type
                FROM flows_flowrun as ffr
                WHERE ffr.flow_id = ce.flow_id
              ) fr_evt on fr_evt.id = fr.id
              WHERE fs.flow_id = ce.flow_id
              GROUP BY fs.flow_id
            ) flow_data) flow_data
          FROM campaigns_campaign as cp LEFT JOIN campaigns_campaignevent as ce on ce.campaign_id = cp.id
          WHERE cp.id = 2 AND ce.event_type = 'F'
          GROUP BY cp.id, ce.flow_id
        ) t GROUP BY t.id;
        """

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            campaign = self.get_object()
            data = Campaign.objects.raw(self.select_data_sql, params=[campaign.id, campaign.id])
            context["current_time"] = timezone.now()
            try:
                context["start_time"] = data[0].start_time
                context["updated_time"] = data[0].updated_time
                context["end_time"] = data[0].updated_time if not data[0].has_running else None
                context["total_contacts"] = data[0].total_contacts
                context["reached_contacts"] = data[0].reached_contacts
                context["remaining_contacts"] = data[0].total_contacts - data[0].reached_contacts
                context["bounces"] = data[0].bounces
                context["inbound"] = data[0].inbound
            except IndexError:
                pass
            return context


class CampaignEventForm(forms.ModelForm):
    event_type = forms.ChoiceField(
        choices=((CampaignEvent.TYPE_MESSAGE, "Send a message"), (CampaignEvent.TYPE_FLOW, "Start a flow")),
        required=True,
        widget=SelectWidget,
    )

    direction = forms.ChoiceField(choices=(("B", "Before"), ("A", "After")), required=True, widget=SelectWidget)

    unit = forms.ChoiceField(choices=CampaignEvent.UNIT_CHOICES, required=True, widget=SelectWidget)

    flow_to_start = TembaChoiceField(
        queryset=Flow.objects.filter(is_active=True),
        required=False,
        empty_label=None,
        widget=SelectWidget(
            attrs={"placeholder": _("Select a flow to start"), "widget_only": True, "searchable": True}
        ),
    )

    relative_to = TembaChoiceField(
        queryset=ContactField.all_fields.none(),
        required=False,
        empty_label=None,
        widget=SelectWidget(
            attrs={
                "placeholder": _("Select a date field to base this event on"),
                "widget_only": True,
                "searchable": True,
            }
        ),
    )

    delivery_hour = forms.ChoiceField(choices=CampaignEvent.get_hour_choices(), required=False, widget=SelectWidget)

    flow_start_mode = forms.ChoiceField(
        choices=(
            (CampaignEvent.MODE_INTERRUPT, _("Stop it and start this event")),
            (CampaignEvent.MODE_SKIP, _("Skip this event")),
        ),
        required=False,
        label=_("If the contact is already active in a flow"),
        widget=SelectWidget,
    )

    message_start_mode = forms.ChoiceField(
        choices=(
            (CampaignEvent.MODE_INTERRUPT, _("Stop it and send the message")),
            (CampaignEvent.MODE_SKIP, _("Skip this message")),
            (CampaignEvent.MODE_PASSIVE, _("Send the message")),
        ),
        required=False,
        widget=SelectWidget(attrs={"widget_only": True}),
    )

    def clean(self):
        data = super().clean()

        if self.data["event_type"] == CampaignEvent.TYPE_MESSAGE:
            if self.languages:
                language = self.languages[0].language
                iso_code = language["iso_code"]
                if iso_code not in self.data or not self.data[iso_code].strip():
                    raise ValidationError(_("A message is required for '%s'") % language["name"])

                for lang_data in self.languages:
                    lang = lang_data.language
                    iso_code = lang["iso_code"]
                    if iso_code in self.data and len(self.data[iso_code].strip()) > Msg.MAX_TEXT_LEN:
                        raise ValidationError(
                            _("Translation for '%(language)s' exceeds the %(limit)d character limit.")
                            % dict(language=lang["name"], limit=Msg.MAX_TEXT_LEN)
                        )
            if not data.get("message_start_mode"):
                self.add_error("message_start_mode", _("This field is required."))
        else:
            if not data.get("flow_to_start"):
                self.add_error("flow_to_start", _("This field is required."))
            if not data.get("flow_start_mode"):
                self.add_error("flow_start_mode", _("This field is required."))

            # validate flow parameters
            flow_params_values = [
                self.data.get(field) for field in self.data.keys() if "flow_parameter_value" in field
            ]
            if flow_params_values and not all(flow_params_values):
                self.add_error(None, _("Flow Parameters are not provided."))

        return data

    def pre_save(self, request, obj):
        org = self.user.get_org()

        # if it's before, negate the offset
        if self.cleaned_data["direction"] == "B":
            obj.offset = -obj.offset

        if self.cleaned_data["unit"] == "H" or self.cleaned_data["unit"] == "M":  # pragma: needs cover
            obj.delivery_hour = -1

        # if its a message flow, set that accordingly
        if self.cleaned_data["event_type"] == CampaignEvent.TYPE_MESSAGE:

            if self.instance.id:
                base_language = self.instance.flow.base_language
            else:
                base_language = org.flow_languages[0] if org.flow_languages else "base"

            translations = {}
            for language in self.languages:
                iso_code = language.language["iso_code"]
                if iso_code in self.cleaned_data and self.cleaned_data.get(iso_code, "").strip():
                    translations[iso_code] = self.cleaned_data.get(iso_code, "").strip()

            if not obj.flow_id or not obj.flow.is_active or not obj.flow.is_system:
                obj.flow = Flow.create_single_message(org, request.user, translations, base_language=base_language)
            else:
                # set our single message on our flow
                obj.flow.update_single_message_flow(self.user, translations, base_language)

            obj.message = translations
            obj.full_clean()
            obj.start_mode = self.cleaned_data["message_start_mode"]

        # otherwise, it's an event that runs an existing flow
        else:
            obj.flow = self.cleaned_data["flow_to_start"]
            obj.start_mode = self.cleaned_data["flow_start_mode"]

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        org = self.user.get_org()

        relative_to = self.fields["relative_to"]
        relative_to.queryset = ContactField.all_fields.filter(
            org=org, is_active=True, value_type=ContactField.TYPE_DATETIME
        ).order_by("label")

        flow = self.fields["flow_to_start"]
        flow.queryset = Flow.objects.filter(
            org=self.user.get_org(),
            flow_type__in=[Flow.TYPE_MESSAGE, Flow.TYPE_VOICE],
            is_active=True,
            is_archived=False,
            is_system=False,
        ).order_by("name")

        message = self.instance.message or {}
        self.languages = []

        # add in all of our languages for message forms
        for lang_code in org.flow_languages:
            lang_name = languages.get_name(lang_code)
            insert = None

            # if it's our primary language, allow use to steal the 'base' message
            if org.flow_languages and org.flow_languages[0] == lang_code:
                initial = message.get(lang_code, "")

                if not initial:
                    initial = message.get("base", "")

                # also, let's show it first
                insert = 0
            else:
                # otherwise, its just a normal language
                initial = message.get(lang_code, "")

            field = forms.CharField(
                widget=CompletionTextarea(attrs={"widget_only": True}),
                required=False,
                label=lang_name,
                initial=initial,
            )

            self.fields[lang_code] = field
            field.language = dict(name=lang_name, iso_code=lang_code)

            # see if we need to insert or append
            if insert is not None:
                self.languages.insert(insert, field)
            else:
                self.languages.append(field)

        # determine our base language if necessary
        base_language = org.flow_languages[0] if org.flow_languages else "base"

        # if we are editing, always include the flow base language
        if self.instance.id:
            base_language = self.instance.flow.base_language

        # add our default language, we'll insert it at the front of the list
        if base_language and base_language not in self.fields:
            field = forms.CharField(
                widget=CompletionTextarea(
                    attrs={
                        "placeholder": _(
                            "Hi @contact.name! This is just a friendly reminder to apply your fertilizer."
                        ),
                        "widget_only": True,
                    }
                ),
                required=False,
                label=_("Default"),
                initial=message.get(base_language),
            )

            self.fields[base_language] = field
            field.language = dict(iso_code=base_language, name="Default")
            self.languages.insert(0, field)

    class Meta:
        model = CampaignEvent
        fields = "__all__"
        exclude = ("extra",)
        widgets = {"offset": InputWidget}


class CampaignEventCRUDL(SmartCRUDL):
    model = CampaignEvent
    actions = ("create", "delete", "read", "update")

    class Read(OrgObjPermsMixin, SmartReadView):
        def pre_process(self, request, *args, **kwargs):
            event = self.get_object()
            if not event.is_active:
                messages.error(self.request, "Campaign event no longer exists")
                return HttpResponseRedirect(reverse("campaigns.campaign_read", args=[event.campaign.pk]))

        def get_object_org(self):
            return self.get_object().campaign.org

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            event_fires = self.get_object().fires.all()

            fired_event_fires = event_fires.exclude(fired=None).order_by("-fired", "pk")
            scheduled_event_fires = event_fires.filter(fired=None).order_by("scheduled", "pk")

            fired = fired_event_fires[:25]
            context["fired_event_fires"] = fired
            context["fired_event_fires_count"] = fired_event_fires.count() - len(fired)

            scheduled = scheduled_event_fires[:25]
            context["scheduled_event_fires"] = scheduled
            context["scheduled_event_fires_count"] = scheduled_event_fires.count() - len(scheduled)

            return context

        def get_gear_links(self):
            links = []

            campaign_event = self.get_object()

            if self.has_org_perm("campaigns.campaignevent_update") and not campaign_event.campaign.is_archived:
                links.append(
                    dict(
                        id="event-update",
                        title=_("Edit"),
                        style="button-primary",
                        js_class="update-event",
                        href=f"{reverse('campaigns.campaignevent_update', args=[campaign_event.id])}",
                        modax=_("Update Event"),
                    )
                )

                if self.has_org_perm("campaigns.campaignevent_delete"):
                    links.append(
                        dict(
                            id="event-delete",
                            title="Delete",
                            href=reverse("campaigns.campaignevent_delete", args=[campaign_event.id]),
                            modax=_("Delete Event"),
                        )
                    )

            return links

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):

        default_template = "smartmin/delete_confirm.html"
        submit_button_name = _("Delete")
        fields = ("uuid",)

        def get_object_org(self):
            return self.get_object().campaign.org

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()
            self.object.release(self.request.user)

            redirect_url = self.get_redirect_url()
            return HttpResponseRedirect(redirect_url)

        def get_redirect_url(self):
            return reverse("campaigns.campaign_read", args=[self.object.campaign.pk])

        def get_cancel_url(self):  # pragma: needs cover
            return reverse("campaigns.campaign_read", args=[self.object.campaign.pk])

    class Update(OrgObjPermsMixin, ModalMixin, SmartUpdateView):
        success_message = ""
        form_class = CampaignEventForm
        submit_button_name = _("Update Event")

        default_fields = [
            "event_type",
            "flow_to_start",
            "offset",
            "unit",
            "direction",
            "relative_to",
            "delivery_hour",
            "message_start_mode",
            "flow_start_mode",
        ]

        def pre_process(self, request, *args, **kwargs):
            event = self.get_object()
            if not event.is_active or not event.campaign.is_active or event.campaign.is_archived:
                raise Http404("Event not found")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def get_object_org(self):
            return self.get_object().campaign.org

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            obj = self.get_object()
            if obj.extra:
                context["flow_parameters_fields"] = "|".join([f"@trigger.params.{key}" for key in obj.extra.keys()])
                context["flow_parameters_values"] = "|".join(obj.extra.values())
            params_context = flow_params_context(self.request)
            context.update(params_context)
            return context

        def derive_fields(self):

            from copy import deepcopy

            fields = deepcopy(self.default_fields)

            # add in all of our flow languages
            org = self.request.user.get_org()
            fields += org.flow_languages

            flow_language = self.object.flow.base_language

            if flow_language not in fields:
                fields.append(flow_language)

            return fields

        def derive_initial(self):
            initial = super().derive_initial()

            if self.object.offset < 0:
                initial["direction"] = "B"
                initial["offset"] = abs(self.object.offset)
            else:
                initial["direction"] = "A"

            if self.object.event_type == "F":
                initial["flow_to_start"] = self.object.flow
                initial["flow_start_mode"] = self.object.start_mode
            else:
                initial["message_start_mode"] = self.object.start_mode

            return initial

        def post_save(self, obj):
            obj = super().post_save(obj)
            obj.update_flow_name()
            return obj

        def pre_save(self, obj):

            obj = super().pre_save(obj)
            self.form.pre_save(self.request, obj)

            prev = CampaignEvent.objects.get(pk=obj.pk)
            if prev.event_type == "M" and (obj.event_type == "F" and prev.flow):  # pragma: needs cover
                flow = prev.flow
                flow.is_active = False
                flow.save()
                obj.message = None

            # if we changed anything, update our event fires
            if (
                prev.unit != obj.unit
                or prev.offset != obj.offset
                or prev.relative_to != obj.relative_to
                or prev.delivery_hour != obj.delivery_hour
                or prev.message != obj.message
                or prev.flow != obj.flow
                or prev.start_mode != obj.start_mode
            ):
                obj = obj.recreate()
                obj.schedule_async()

            if obj.event_type == CampaignEvent.TYPE_FLOW:
                flow_params_fields = [field for field in self.request.POST.keys() if "flow_parameter_field" in field]
                flow_params_values = [field for field in self.request.POST.keys() if "flow_parameter_value" in field]

                params = build_flow_parameters(self.request.POST, flow_params_fields, flow_params_values)
                obj.extra = params if params else None

            return obj

        def get_success_url(self):
            return reverse("campaigns.campaignevent_read", args=[self.object.pk])

    class Create(OrgPermsMixin, ModalMixin, SmartCreateView):

        default_fields = [
            "event_type",
            "flow_to_start",
            "offset",
            "unit",
            "direction",
            "relative_to",
            "delivery_hour",
            "message_start_mode",
            "flow_start_mode",
        ]
        form_class = CampaignEventForm
        success_message = ""
        template_name = "campaigns/campaignevent_update.haml"
        submit_button_name = _("Add Event")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            params_context = flow_params_context(self.request)
            context.update(params_context)
            return context

        def pre_process(self, request, *args, **kwargs):
            campaign_id = request.GET.get("campaign", None)
            if campaign_id:
                campaign = Campaign.objects.filter(id=campaign_id, is_active=True, is_archived=False)

                if not campaign.exists():
                    raise Http404("Campaign not found")

        def derive_fields(self):

            from copy import deepcopy

            fields = deepcopy(self.default_fields)

            # add in all of our flow languages
            org = self.request.user.get_org()

            if org.flow_languages:
                fields += org.flow_languages
            else:
                fields.append("base")

            return fields

        def get_success_url(self):
            return reverse("campaigns.campaign_read", args=[self.object.campaign.pk])

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            initial["unit"] = "D"
            initial["offset"] = "15"
            initial["direction"] = "A"
            initial["event_type"] = "M"
            initial["message_start_mode"] = "I"
            initial["delivery_hour"] = "-1"
            initial["flow_start_mode"] = "I"

            # default to our first date field
            initial["relative_to"] = ContactField.all_fields.filter(
                org=self.request.user.get_org(), is_active=True, value_type=ContactField.TYPE_DATETIME
            ).first()

            return initial

        def post_save(self, obj):
            obj = super().post_save(obj)
            obj.update_flow_name()
            obj.schedule_async()
            return obj

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.campaign = Campaign.objects.get(org=self.request.user.get_org(), pk=self.request.GET.get("campaign"))

            if obj.event_type == CampaignEvent.TYPE_FLOW:
                flow_params_fields = [field for field in self.request.POST.keys() if "flow_parameter_field" in field]
                flow_params_values = [field for field in self.request.POST.keys() if "flow_parameter_value" in field]

                params = build_flow_parameters(self.request.POST, flow_params_fields, flow_params_values)
                obj.extra = params if params else None

            self.form.pre_save(self.request, obj)
            return obj

        def form_invalid(self, form):
            return super().form_invalid(form)
