import datetime
import math
from datetime import timedelta

from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView, SmartTemplateView, SmartUpdateView

from django import forms
from django.db.models import Min
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ngettext_lazy, ugettext_lazy as _
from django.utils import timezone

from temba.channels.models import Channel
from temba.contacts.models import ContactGroup, ContactURN
from temba.contacts.search.omnibox import omnibox_serialize, omnibox_deserialize
from temba.flows.models import Flow
from temba.formax import FormaxMixin
from temba.msgs.views import ModalMixin
from temba.orgs.views import OrgFilterMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.schedules.models import Schedule
from temba.utils.fields import (
    InputWidget,
    SelectMultipleWidget,
    SelectWidget,
    TembaChoiceField,
    TembaMultipleChoiceField,
)
from temba.utils.views import BulkActionMixin, ComponentFormMixin
from temba.utils import build_flow_parameters, analytics, flow_params_context, chunk_list
from temba.utils.fields import CompletionTextarea, JSONField, OmniboxChoice

from .models import Trigger
from ..utils.json import JsonResponse


class FlowParamsMixin:
    def pre_save(self, obj, *args, **kwargs):
        obj = super().pre_save(obj, *args, **kwargs)
        obj.extra = self.flow_params
        return obj

    def get_context_data(self, *args, **kwargs):
        context = super().get_context_data(*args, **kwargs)
        try:
            obj = self.get_object()
            if obj.extra:
                context["flow_parameters_fields"] = "|".join([f"@trigger.params.{key}" for key in obj.extra.keys()])
                context["flow_parameters_values"] = "|".join(obj.extra.values())
            params_context = flow_params_context(self.request)
            context.update(params_context)
        except AttributeError:
            pass
        return context

    @property
    def flow_params(self):
        flow_params_fields = [field for field in self.request.POST.keys() if "flow_parameter_field" in field]
        flow_params_values = [field for field in self.request.POST.keys() if "flow_parameter_value" in field]
        params = build_flow_parameters(self.request.POST, flow_params_fields, flow_params_values)
        return params if params else None


class BaseTriggerForm(forms.ModelForm):
    """
    Base form for different trigger types
    """

    flow = TembaChoiceField(
        Flow.objects.none(),
        label=_("Flow"),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select a flow"), "searchable": True}),
    )

    groups = TembaMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(),
        label=_("Groups To Include"),
        help_text=_("Only includes contacts in these groups."),
        required=False,
        widget=SelectMultipleWidget(
            attrs={"icons": True, "placeholder": _("Optional: Select contact groups"), "searchable": True}
        ),
    )
    exclude_groups = TembaMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(),
        label=_("Groups To Exclude"),
        help_text=_("Excludes contacts in these groups."),
        required=False,
        widget=SelectMultipleWidget(
            attrs={"icons": True, "placeholder": _("Optional: Select contact groups"), "searchable": True}
        ),
    )

    def __init__(self, user, trigger_type, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.user = user
        self.org = user.get_org()
        self.trigger_type = Trigger.get_type(code=trigger_type)

        flow_types = self.trigger_type.allowed_flow_types
        flows = self.org.flows.filter(flow_type__in=flow_types, is_active=True, is_archived=False, is_system=False)

        self.fields["flow"].queryset = flows.order_by("name")

        groups = ContactGroup.get_user_groups(self.org, ready_only=False)

        self.fields["groups"].queryset = groups
        self.fields["exclude_groups"].queryset = groups

    def get_channel_choices(self, schemes):
        return self.org.channels.filter(is_active=True, schemes__overlap=list(schemes)).order_by("name")

    def get_conflicts(self, cleaned_data):
        conflicts = Trigger.get_conflicts(self.org, self.trigger_type.code, **self.get_conflicts_kwargs(cleaned_data))

        # if we're editing a trigger we can't conflict with ourselves
        if self.instance:
            conflicts = conflicts.exclude(id=self.instance.id)

        return conflicts

    def get_conflicts_kwargs(self, cleaned_data):
        return {"groups": cleaned_data.get("groups", [])}

    def clean_keyword(self):
        keyword = self.cleaned_data.get("keyword") or ""
        keyword = keyword.strip()

        if not self.trigger_type.is_valid_keyword(keyword):
            raise forms.ValidationError(_("Must be a single word containing only letters and numbers."))

        return keyword.lower()

    def clean(self):
        cleaned_data = super().clean()

        groups = cleaned_data.get("groups", [])
        exclude_groups = cleaned_data.get("exclude_groups", [])

        if set(groups).intersection(exclude_groups):
            raise forms.ValidationError(_("Can't include and exclude the same group."))

        # only check for conflicts if user is submitting valid data for all fields
        if not self.errors and self.get_conflicts(cleaned_data):
            raise forms.ValidationError(_("There already exists a trigger of this type with these options."))

        # validate flow parameters
        flow_params_values = [
            cleaned_data.get(field) for field in cleaned_data.keys() if "flow_parameter_value" in field
        ]
        if flow_params_values and not all(flow_params_values):
            raise forms.ValidationError(_("Flow Parameters are not provided."))
        return cleaned_data

    class Meta:
        model = Trigger
        fields = ("flow", "groups", "exclude_groups")


class RegisterTriggerForm(BaseTriggerForm):
    """
    Wizard form that creates keyword trigger which starts contacts in a newly created flow which adds them to a group
    """

    class AddNewGroupChoiceField(TembaChoiceField):
        def clean(self, value):
            if value.startswith("[_NEW_]"):  # pragma: needs cover
                value = value[7:]

                # we must get groups for this org only
                group = ContactGroup.get_user_group_by_name(self.user.get_org(), value)
                if not group:
                    group = ContactGroup.create_static(self.user.get_org(), self.user, name=value)
                return group

            return super().clean(value)

    keyword = forms.CharField(
        max_length=16,
        required=True,
        label=_("Join Keyword"),
        help_text=_("The first word of the message"),
        widget=InputWidget(),
    )

    action_join_group = AddNewGroupChoiceField(
        ContactGroup.user_groups.none(),
        required=True,
        label=_("Group to Join"),
        help_text=_("The group the contact will join when they send the above keyword"),
        widget=SelectWidget(),
    )

    response = forms.CharField(
        widget=CompletionTextarea(attrs={"placeholder": _("Hi @contact.name!")}),
        required=False,
        label=ngettext_lazy("Response", "Responses", 1),
        help_text=_("The message to send in response after they join the group (optional)"),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(user, Trigger.TYPE_KEYWORD, *args, **kwargs)

        # on this form flow becomes the flow to be triggered from the generated flow and is optional
        self.fields["flow"].required = False

        self.fields["action_join_group"].queryset = ContactGroup.user_groups.filter(
            org=self.org, is_active=True
        ).order_by("name")
        self.fields["action_join_group"].user = user

    def get_conflicts_kwargs(self, cleaned_data):
        kwargs = super().get_conflicts_kwargs(cleaned_data)
        kwargs["keyword"] = cleaned_data.get("keyword") or ""
        return kwargs

    class Meta(BaseTriggerForm.Meta):
        fields = ("keyword", "action_join_group", "response", "flow") + BaseTriggerForm.Meta.fields


class ScheduleTriggerForm(forms.ModelForm):
    repeat_period = forms.ChoiceField(
        choices=Schedule.REPEAT_CHOICES, label="Repeat", required=False, widget=SelectWidget()
    )

    repeat_days_of_week = forms.MultipleChoiceField(
        choices=Schedule.REPEAT_DAYS_CHOICES,
        label="Repeat Days",
        required=False,
        widget=SelectMultipleWidget(attrs=({"placeholder": _("Select days to repeat on")})),
    )

    start_datetime = forms.DateTimeField(
        required=False,
        label=_("Start Time"),
        widget=InputWidget(attrs={"datetimepicker": True, "placeholder": "Select a time to start the flow"}),
    )

    flow = forms.ModelChoiceField(
        Flow.objects.none(),
        label=_("Flow"),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select a flow"), "searchable": True}),
        empty_label=None,
    )

    start_datetime_value = forms.IntegerField(required=False)

    omnibox = JSONField(
        label=_("Contacts"),
        required=True,
        help_text=_("The groups and contacts the flow will be broadcast to"),
        widget=OmniboxChoice(
            attrs={"placeholder": _("Recipients, enter contacts or groups"), "groups": True, "contacts": True}
        ),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        org = user.get_org()
        flows = Flow.get_triggerable_flows(org, by_schedule=True)

        self.fields["start_datetime"].help_text = _("%s Time Zone" % org.timezone)
        self.fields["flow"].queryset = flows.order_by("name")

    def clean_repeat_days_of_week(self):
        return "".join(self.cleaned_data["repeat_days_of_week"])

    def clean_omnibox(self):
        return omnibox_deserialize(self.user.get_org(), self.cleaned_data["omnibox"])

    class Meta:
        model = Trigger
        fields = ("flow", "omnibox", "repeat_period", "repeat_days_of_week", "start_datetime")


class ScheduleTriggerInBatchForm(ScheduleTriggerForm):
    BATCH_INTERVAL = (
        (5, _("5 minutes")),
        (10, _("10 minutes")),
        (15, _("15 minutes")),
        (20, _("20 minutes")),
        (25, _("25 minutes")),
        (30, _("30 minutes")),
    )

    start_datetime = forms.DateTimeField(
        required=True,
        label=_("Start Time"),
        widget=InputWidget(attrs={"datetimepicker": True, "placeholder": "Select a time to start the flow"}),
    )

    omnibox = JSONField(
        label=_("Contacts"),
        required=True,
        help_text=_("The groups and contacts the flow will be broadcast to"),
        widget=OmniboxChoice(attrs={"placeholder": _("Recipients, enter groups"), "groups": True}),
    )

    batch_interval = forms.ChoiceField(
        choices=BATCH_INTERVAL, label="Batch Interval", required=True, widget=SelectWidget()
    )


class NewConversationTriggerForm(BaseTriggerForm):
    """
    Form for New Conversation triggers
    """

    channel = forms.ModelChoiceField(Channel.objects.filter(pk__lt=0), label=_("Channel"), required=True)

    def __init__(self, user, *args, **kwargs):
        flows = Flow.get_triggerable_flows(user.get_org(), by_schedule=False)
        super().__init__(user, flows, *args, **kwargs)

        self.fields["channel"].queryset = Channel.objects.filter(
            is_active=True,
            org=self.user.get_org(),
            schemes__overlap=list(ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION),
        )

    def clean_channel(self):
        channel = self.cleaned_data["channel"]
        existing = Trigger.objects.filter(
            org=self.user.get_org(),
            is_active=True,
            is_archived=False,
            trigger_type=Trigger.TYPE_NEW_CONVERSATION,
            channel=channel,
        )
        if self.instance:
            existing = existing.exclude(id=self.instance.id)

        if existing.exists():
            raise forms.ValidationError(_("Trigger with this Channel already exists."))

        return self.cleaned_data["channel"]

    class Meta(BaseTriggerForm.Meta):
        fields = ("channel", "flow")


class BaseLargeSendForm(forms.ModelForm):
    BATCH_INTERVAL = (
        (5, _("5 minutes")),
        (10, _("10 minutes")),
        (15, _("15 minutes")),
        (20, _("20 minutes")),
        (25, _("25 minutes")),
        (30, _("30 minutes")),
        (45, _("45 minutes")),
        (60, _("1 hour")),
    )

    batch_interval = forms.ChoiceField(
        choices=BATCH_INTERVAL,
        label=_("Batch Interval"),
        help_text=_("I want to stagger each chunk by this much"),
        required=True,
        widget=SelectWidget(),
    )
    flow = TembaChoiceField(
        Flow.objects.none(),
        label=_("Flow"),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select a flow"), "searchable": True}),
    )
    groups = TembaMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(),
        label=_("Groups"),
        help_text=_("Only includes contacts in these groups."),
        required=True,
        widget=SelectMultipleWidget(
            attrs={"icons": True, "placeholder": _("Select contact groups"), "searchable": True}
        ),
    )
    start_time = forms.DateTimeField(
        required=True,
        label=_("Start Time for send"),
        widget=InputWidget(attrs={"datetimepicker": True, "placeholder": _("Select a date and time")}),
    )
    chunk_size = forms.IntegerField(
        label=_("Chunks"),
        help_text=_("I want to split the message to this many pieces"),
        widget=InputWidget(attrs={"type": "number", "placeholder": _("Enter chunk size")}),
    )

    limit_time = forms.BooleanField(required=False, label=_("Limit to business hours"), help_text=_("9 AM to 5 PM"))

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.org = user.get_org()
        flow_types = (Flow.TYPE_MESSAGE, Flow.TYPE_VOICE)
        flows = self.org.flows.filter(flow_type__in=flow_types, is_active=True, is_archived=False, is_system=False)

        self.fields["flow"].queryset = flows.order_by("name")
        self.fields["groups"].queryset = ContactGroup.get_user_groups(self.org, ready_only=False)

    class Meta:
        model = Trigger
        fields = ("flow", "groups", "start_time", "batch_interval", "chunk_size", "limit_time")


class LargeSendMixin:
    NINE_AM = 9
    FIVE_PM = 17

    @classmethod
    def get_new_time(cls, input_time, hour, add_to_day=0):
        input_time = datetime.datetime(
            year=input_time.year, month=input_time.month, day=input_time.day + add_to_day, hour=hour
        )
        return timezone.make_aware(input_time)

    def derive_start_time(self, start_time, limit_time):
        # get 9am local time and compare with current time if work hour constraint is set
        opening_time = self.get_new_time(start_time, self.NINE_AM)
        closing_time = self.get_new_time(start_time, self.FIVE_PM)

        if limit_time and start_time < opening_time:
            start_time = opening_time

        if limit_time and start_time > closing_time:
            start_time = self.get_new_time(start_time, self.NINE_AM, add_to_day=1)
        return start_time

    def calculate_schedule_time(self, start_datetime, limit_time, batch_interval, chunk_size):
        start_time = self.derive_start_time(start_datetime, limit_time)
        schedule_time_list = []

        for count in range(chunk_size):
            if count > 0:
                start_time = start_time + timedelta(minutes=int(batch_interval))
            # get 5pm local time and compare with current time if work hour constraint is set
            closing_time = self.get_new_time(start_time, self.FIVE_PM)
            if limit_time and start_time > closing_time:
                start_time = self.get_new_time(start_time, self.NINE_AM, add_to_day=1)
            schedule_time_list.append(start_time)

        return schedule_time_list


class TriggerCRUDL(SmartCRUDL):
    model = Trigger
    actions = (
        "create",
        "create_keyword",
        "create_register",
        "create_catchall",
        "create_schedule",
        "create_schedule_in_batch",
        "create_inbound_call",
        "create_missed_call",
        "create_new_conversation",
        "create_referral",
        "create_closed_ticket",
        "update",
        "list",
        "archived",
        "type",
        "create_large_send",
        "large_send_schedule_summary",
    )

    class Create(FormaxMixin, OrgFilterMixin, OrgPermsMixin, SmartTemplateView):
        title = _("Create Trigger")

        def derive_formax_sections(self, formax, context):
            def add_section(name, url, icon):
                formax.add_section(name, reverse(url), icon=icon, action="redirect", button=_("Create Trigger"))

            org_schemes = self.org.get_schemes(Channel.ROLE_RECEIVE)
            add_section("trigger-keyword", "triggers.trigger_create_keyword", "icon-tree")
            add_section("trigger-register", "triggers.trigger_create_register", "icon-users-2")
            add_section("trigger-catchall", "triggers.trigger_create_catchall", "icon-bubble")
            add_section("trigger-schedule", "triggers.trigger_create_schedule", "icon-clock")
            add_section("trigger-schedule-in-batch", "triggers.trigger_create_schedule_in_batch", "icon-wand")
            add_section("trigger-inboundcall", "triggers.trigger_create_inbound_call", "icon-phone2")
            add_section("trigger-missedcall", "triggers.trigger_create_missed_call", "icon-phone")

            if ContactURN.SCHEMES_SUPPORTING_NEW_CONVERSATION.intersection(org_schemes):
                add_section("trigger-new-conversation", "triggers.trigger_create_new_conversation", "icon-bubbles-2")

            if ContactURN.SCHEMES_SUPPORTING_REFERRALS.intersection(org_schemes):
                add_section("trigger-referral", "triggers.trigger_create_referral", "icon-exit")

            add_section("trigger-closed-ticket", "triggers.trigger_create_closed_ticket", "icon-ticket")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            query_params = self.request.GET
            trigger = query_params.get("trigger")
            flow = query_params.get("flow")

            if trigger in ("keyword", "schedule") and flow:
                context["trigger"] = trigger
                context["flow"] = flow

            return context

    class BaseCreate(OrgPermsMixin, ComponentFormMixin, FlowParamsMixin, SmartCreateView):
        trigger_type = None
        permission = "triggers.trigger_create"
        success_url = "@triggers.trigger_list"
        success_message = ""

        def get_form_class(self):
            return self.form_class or Trigger.get_type(code=self.trigger_type).form

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user

            from .types import TYPES_BY_CODE

            _trigger_type = TYPES_BY_CODE.get(self.trigger_type)
            if _trigger_type:
                kwargs["auto_id"] = f"id_{_trigger_type.slug}_%s"
            return kwargs

        def get_create_kwargs(self, user, cleaned_data):
            return {}

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()
            flow = form.cleaned_data["flow"]
            groups = form.cleaned_data["groups"]
            exclude_groups = form.cleaned_data["exclude_groups"]

            Trigger.create(
                org,
                user,
                form.trigger_type.code,
                flow,
                groups=groups,
                exclude_groups=exclude_groups,
                extra=self.flow_params,
                **self.get_create_kwargs(user, form.cleaned_data),
            )

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class CreateKeyword(BaseCreate):
        trigger_type = Trigger.TYPE_KEYWORD

        def get_create_kwargs(self, user, cleaned_data):
            return {"keyword": cleaned_data["keyword"]}

    class CreateRegister(BaseCreate):
        form_class = RegisterTriggerForm

        def form_valid(self, form):
            keyword = form.cleaned_data["keyword"]
            join_group = form.cleaned_data["action_join_group"]
            start_flow = form.cleaned_data["flow"]
            send_msg = form.cleaned_data["response"]
            groups = form.cleaned_data["groups"]
            exclude_groups = form.cleaned_data["exclude_groups"]

            org = self.request.user.get_org()
            register_flow = Flow.create_join_group(org, self.request.user, join_group, send_msg, start_flow)

            Trigger.create(
                org,
                self.request.user,
                Trigger.TYPE_KEYWORD,
                register_flow,
                groups=groups,
                exclude_groups=exclude_groups,
                keyword=keyword,
                extra=self.flow_params,
            )

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class CreateCatchall(BaseCreate):
        trigger_type = Trigger.TYPE_CATCH_ALL

    class CreateSchedule(BaseCreate):
        trigger_type = Trigger.TYPE_SCHEDULE

        def get_create_kwargs(self, user, cleaned_data):
            start_time = cleaned_data["start_datetime"]
            repeat_period = cleaned_data["repeat_period"]
            repeat_days_of_week = cleaned_data["repeat_days_of_week"]

            schedule = Schedule.create_schedule(
                user.get_org(), user, start_time, repeat_period, repeat_days_of_week=repeat_days_of_week
            )

            return {"schedule": schedule, "contacts": cleaned_data["contacts"]}

    class CreateScheduleInBatch(BaseCreate):
        trigger_type = Trigger.TYPE_SCHEDULE_IN_BATCH

        def create_trigger(self, start_time, org, form):
            schedule = Schedule.create_schedule(
                org,
                self.request.user,
                start_time,
                form.cleaned_data.get("repeat_period"),
                repeat_days_of_week=form.cleaned_data.get("repeat_days_of_week"),
            )

            return Trigger.objects.create(
                flow=self.form.cleaned_data["flow"],
                org=self.request.user.get_org(),
                schedule=schedule,
                trigger_type=Trigger.TYPE_SCHEDULE,
                created_by=self.request.user,
                modified_by=self.request.user,
                extra=self.flow_params,
            )

        def form_valid(self, form):
            analytics.track(self.request.user, "temba.trigger_created", dict(type="schedule"))
            org = self.request.user.get_org()
            start_time = form.cleaned_data["start_datetime"]
            groups = self.form.cleaned_data["groups"]
            exclude_groups = self.form.cleaned_data["exclude_groups"]
            batch_interval = self.form.cleaned_data["batch_interval"]
            triggers = []
            count = 0

            group_order = self.request.POST.get("group_order", [])
            sorted_groups = sorted(groups, key=lambda x: group_order.index(str(x.id)))

            for group in sorted_groups:
                if count > 0:
                    start_time = start_time + timedelta(minutes=int(batch_interval))
                group_trigger = self.create_trigger(start_time, org, form)
                group_trigger.groups.add(group)
                for exclude_group in exclude_groups:
                    group_trigger.exclude_groups.add(exclude_group)
                triggers.append(group_trigger)
                count += 1

            self.post_save(triggers)

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class CreateInboundCall(BaseCreate):
        trigger_type = Trigger.TYPE_INBOUND_CALL

    class CreateMissedCall(BaseCreate):
        trigger_type = Trigger.TYPE_MISSED_CALL

    class CreateNewConversation(BaseCreate):
        trigger_type = Trigger.TYPE_NEW_CONVERSATION

        def get_create_kwargs(self, user, cleaned_data):
            return {"channel": cleaned_data["channel"]}

    class CreateReferral(BaseCreate):
        trigger_type = Trigger.TYPE_REFERRAL

        def get_create_kwargs(self, user, cleaned_data):
            return {"channel": cleaned_data["channel"], "referrer_id": cleaned_data["referrer_id"]}

    class CreateClosedTicket(BaseCreate):
        trigger_type = Trigger.TYPE_CLOSED_TICKET

    class Update(ModalMixin, ComponentFormMixin, OrgObjPermsMixin, FlowParamsMixin, SmartUpdateView):
        success_message = ""

        def get_form_class(self):
            return self.object.type.form

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()

            if self.object.trigger_type == Trigger.TYPE_SCHEDULE:
                schedule = self.object.schedule
                days_of_the_week = list(schedule.repeat_days_of_week) if schedule.repeat_days_of_week else []
                contacts = self.object.contacts.all()

                initial["start_datetime"] = schedule.next_fire
                initial["repeat_period"] = schedule.repeat_period
                initial["repeat_days_of_week"] = days_of_the_week
                initial["contacts"] = omnibox_serialize(self.object.org, (), contacts)
            return initial

        def form_valid(self, form):
            if self.object.trigger_type == Trigger.TYPE_SCHEDULE:
                self.object.schedule.update_schedule(
                    form.cleaned_data["start_datetime"],
                    form.cleaned_data["repeat_period"],
                    form.cleaned_data.get("repeat_days_of_week"),
                )

            response = super().form_valid(form)
            response["REDIRECT"] = self.get_success_url()
            return response

    class BaseList(OrgFilterMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
        """
        Base class for list views
        """

        fields = ("name",)
        default_template = "triggers/trigger_list.html"
        search_fields = ("keyword__icontains", "flow__name__icontains", "channel__name__icontains")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.request.user.get_org()
            context["main_folders"] = self.get_main_folders(org)
            context["type_folders"] = self.get_type_folders(org)
            context["request_url"] = self.request.path
            return context

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = (
                qs.filter(is_active=True)
                .annotate(earliest_group=Min("groups__name"))
                .order_by("keyword", "earliest_group", "id")
                .select_related("flow", "channel")
                .prefetch_related("contacts", "groups")
            )
            return qs

        def get_main_folders(self, org):
            return [
                dict(
                    label=_("All"),
                    url=reverse("triggers.trigger_list"),
                    count=org.triggers.filter(is_active=True, is_archived=False).count(),
                ),
                dict(
                    label=_("Archived"),
                    url=reverse("triggers.trigger_archived"),
                    count=org.triggers.filter(is_active=True, is_archived=True).count(),
                ),
            ]

        def get_type_folders(self, org):
            from .types import TYPES_BY_SLUG

            org_triggers = org.triggers.filter(is_active=True, is_archived=False)
            folders = []
            for slug, trigger_type in TYPES_BY_SLUG.items():
                # skip schedule in batch
                if trigger_type.code == "B":
                    continue
                folders.append(
                    dict(
                        label=trigger_type.name,
                        url=reverse("triggers.trigger_type", kwargs={"type": slug}),
                        count=org_triggers.filter(trigger_type=trigger_type.code).count(),
                    )
                )
            return folders

    class List(BaseList):
        """
        Non-archived triggers of all types
        """

        bulk_actions = ("archive",)
        title = _("Triggers")

        def pre_process(self, request, *args, **kwargs):
            # if they have no triggers and no search performed, send them to create page
            obj_count = super().get_queryset(*args, **kwargs).count()
            if obj_count == 0 and not request.GET.get("search", ""):
                return HttpResponseRedirect(reverse("triggers.trigger_create"))
            return super().pre_process(request, *args, **kwargs)

        def get_queryset(self, *args, **kwargs):
            return super().get_queryset(*args, **kwargs).filter(is_archived=False)

    class Archived(BaseList):
        """
        Archived triggers of all types
        """

        bulk_actions = ("restore",)
        title = _("Archived Triggers")

        def get_queryset(self, *args, **kwargs):
            return super().get_queryset(*args, **kwargs).filter(is_active=True, is_archived=True)

    class Type(BaseList):
        """
        Type filtered list view
        """

        bulk_actions = ("archive",)

        @classmethod
        def derive_url_pattern(cls, path, action):
            from .types import TYPES_BY_SLUG

            return rf"^%s/%s/(?P<type>{'|'.join(TYPES_BY_SLUG.keys())}+)/$" % (path, action)

        @property
        def trigger_type(self):
            return Trigger.get_type(slug=self.kwargs["type"])

        def derive_title(self):
            return self.trigger_type.title

        def get_queryset(self, *args, **kwargs):
            return super().get_queryset(*args, **kwargs).filter(is_archived=False, trigger_type=self.trigger_type.code)

    class CreateLargeSend(OrgPermsMixin, ComponentFormMixin, FlowParamsMixin, LargeSendMixin, SmartCreateView):
        form_class = BaseLargeSendForm
        success_url = "@triggers.trigger_list"
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def create_trigger(self, start_time, org):
            user = self.request.user
            schedule = Schedule.create_schedule(
                org,
                user,
                start_time,
                Schedule.REPEAT_NEVER,
            )

            return Trigger.objects.create(
                flow=self.form.cleaned_data["flow"],
                org=org,
                schedule=schedule,
                trigger_type=Trigger.TYPE_SCHEDULE,
                created_by=user,
                modified_by=user,
                extra=self.flow_params,
            )

        @classmethod
        def merge_groups_contacts(cls, groups):
            contact_list = []
            for group in groups:
                contact_list.append(set(group.contacts.values_list("id", flat=True)))

            return list(set().union(*contact_list))

        @classmethod
        def group_contacts(cls, cleaned_data, contacts, user):
            org = user.get_org()

            start_time = cleaned_data["start_time"]
            chunk_size = cleaned_data["chunk_size"]

            created_groups = []
            group_name = f"Large Send - {start_time.strftime('%Y-%m-%d')}"

            max_in_group = math.ceil(len(contacts) / chunk_size)
            chunk_count = 1
            for contacts_chunk in chunk_list(contacts, max_in_group):
                chunk_group_name = f"{group_name} - {chunk_count}"
                chunk_group_name = ContactGroup.get_unique_name(org, chunk_group_name)
                group = ContactGroup.get_or_create(org, user, chunk_group_name)
                group.contacts.add(*contacts_chunk)
                created_groups.append(group)
                chunk_count += 1

            return created_groups

        def form_valid(self, form):
            cleaned_data = form.cleaned_data
            user = self.request.user
            org = user.get_org()
            start_time = cleaned_data["start_time"]
            groups = cleaned_data["groups"]
            batch_interval = cleaned_data["batch_interval"]
            chunk_size = cleaned_data["chunk_size"]
            limit_time = cleaned_data["limit_time"]

            contacts = self.merge_groups_contacts(groups)
            created_groups = self.group_contacts(cleaned_data, contacts, user)
            schedule_time_list = self.calculate_schedule_time(start_time, limit_time, batch_interval, chunk_size)

            triggers = []
            count = 0

            for group in created_groups:
                schedule_time = schedule_time_list[count]
                group_trigger = self.create_trigger(schedule_time, org)
                group_trigger.groups.add(group)
                triggers.append(group_trigger)
                count += 1

            self.post_save(triggers)

            response = self.render_to_response(self.get_context_data(form=form))
            response["REDIRECT"] = self.get_success_url()
            return response

    class LargeSendScheduleSummary(OrgPermsMixin, LargeSendMixin, SmartListView):
        def post(self, request, *args, **kwargs):
            response_info = dict(schedule_time_list=[])
            batch_interval = self.request.POST.get("batch_interval")
            start_time = self.request.POST.get("start_time")
            limit_time = self.request.POST.get("limit_time", "false")
            groups = self.request.POST.get("groups", "").split(",")
            chunk_size = int(self.request.POST.get("chunk_size"))
            contacts = []

            for group_id in groups:
                group = ContactGroup.user_groups.filter(id=group_id, org=self.org).first()
                contacts.append(set(group.contacts.values_list("id", flat=True)))

            contacts = list(set().union(*contacts))
            total_contacts = len(contacts)
            start_time = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M")
            start_time = timezone.make_aware(start_time)
            limit_time = limit_time == "true"
            response_info["total_contacts"] = total_contacts
            response_info["max_contacts_per_group"] = math.ceil(total_contacts / chunk_size)
            response_info["chunk_size"] = chunk_size
            response_info["schedule_time_list"] = self.calculate_schedule_time(
                start_time, limit_time, batch_interval, chunk_size
            )

            return JsonResponse(response_info)
