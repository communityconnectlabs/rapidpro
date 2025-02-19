from django import forms
from django.utils.translation import gettext_lazy as _
from smartmin.views import SmartCRUDL, SmartListView, SmartCreateView, SmartUpdateView, SmartDeleteView
from .models import CustomerEventConfig, CustomerEventHandler, HTTPMethod
from ..orgs.models import Org
from ..utils.views import BulkActionMixin
from ..utils.fields import InputWidget, SelectWidget, SelectMultipleWidget, CheckboxWidget


class CustomerEventConfigForm(forms.ModelForm):
    method = forms.ChoiceField(
        label="Method",
        widget=SelectWidget(attrs=({"placeholder": _("Select method")})),
        choices=[(m.value, m.value) for m in HTTPMethod],
    )
    action = forms.ChoiceField(
        label="Action",
        widget=SelectWidget(attrs=({"placeholder": _("Select action"), "searchable": True})),
    )
    action_description = forms.CharField(
        label="Action Description",
        widget=InputWidget(attrs={"placeholder": _("Enter action description")}),
    )
    handlers = forms.MultipleChoiceField(
        choices=CustomerEventHandler.choices(),
        label="Handlers",
        widget=SelectMultipleWidget(attrs=({"placeholder": _("Select event handlers")})),
    )
    org = forms.ModelChoiceField(
        label="Organization",
        widget=SelectWidget(attrs=({"placeholder": _("Select organization"), "searchable": True})),
        queryset=Org.objects.all(),
        blank=True,
        required=False,
    )
    system_wide = forms.BooleanField(
        label="System Wide",
        widget=CheckboxWidget(attrs={"placeholder": _("Tick to make system wide")}),
        required=False,
    )
    notification_template = forms.CharField(
        label="Notification Template",
        widget=InputWidget(attrs={"placeholder": _("Enter notification template"), "textarea": True}),
        required=False,
    )
    handlers_configs = forms.JSONField(
        label="Handlers Configs",
        widget=forms.HiddenInput(),
        required=False,
    )

    class Meta:
        model = CustomerEventConfig
        fields = [
            "method", "action", "action_description", "handlers", "system_wide", "org", "notification_template",
            "handlers_configs",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["action"].choices = CustomerEventConfig.action_choices()

    def clean_handlers_configs(self):
        handlers_configs = self.cleaned_data.get("handlers_configs") or {}
        handlers = self.cleaned_data.get("handlers") or []
        for handler in handlers:
            if handler not in handlers_configs:
                handlers_configs[handler] = {}
        return handlers_configs


class CustomerEventsCRUDL(SmartCRUDL):
    model = CustomerEventConfig
    actions = ('list', 'create', 'update', 'delete')

    class List(BulkActionMixin, SmartListView):
        search_fields = ("action", "action_description")
        link_fields = ("action", "action_description")
        fields = ("method", "action", "action_description", "handlers", "org", "system_wide")
        results_title = 'Customer Event Configurations'

    class Create(SmartCreateView):
        form_class = CustomerEventConfigForm

    class Update(SmartUpdateView):
        form_class = CustomerEventConfigForm

    class Delete(SmartDeleteView):
        def get_success_url(self):
            return reversed("events.customereventconfig_list")
        def get_cancel_url(self):
            return reversed("events.customereventconfig_list")

        def get_redirect_url(self, **kwargs):
            return self.get_success_url()
