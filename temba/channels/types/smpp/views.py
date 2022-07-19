from smartmin.views import SmartFormView

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        name = forms.CharField(label="Channel Name")
        sms_center = forms.CharField(label=_("SMS Center"), help_text=_("Url of the SMSC service"))
        system_id = forms.CharField()
        password = forms.CharField()
        phone_number = forms.CharField(
            label=_("SMPP Sender Phone Number"), help_text=_("The Number from which users will receive messages.")
        )
        system_type = forms.CharField(required=False)

        def clean(self):
            org = self.request.user.get_org()
            cleaned_data = super().clean()

            for channel in Channel.objects.filter(org=org, is_active=True, channel_type=self.channel_type.code):
                cred_equal = all(
                    [
                        channel.config.get("sms_center", "") == cleaned_data.get("sms_center", ""),
                        channel.config.get("system_id", "") == cleaned_data.get("system_id", ""),
                    ]
                )
                if cred_equal:
                    error = ValidationError(_("A SMPP channel with this credentials already exists."))
                    self.add_error("sms_center", error)
                    self.add_error("system_id", error)

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()

        self.object = Channel.create(
            org,
            self.request.user,
            None,
            self.channel_type,
            name=self.form.cleaned_data.get("name", "SMPP Channel"),
            address=self.form.cleaned_data.get("phone_number", ""),
            config={
                "sms_center": self.form.cleaned_data.get("sms_center", ""),
                "system_id": self.form.cleaned_data.get("system_id", ""),
                "password": self.form.cleaned_data.get("password", ""),
                "phone_number": self.form.cleaned_data.get("phone_number", ""),
            },
        )
        return super().form_valid(form)
