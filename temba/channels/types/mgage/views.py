import phonenumbers

from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.utils.fields import SelectWidget

from ...models import Channel
from ...views import ClaimViewMixin, ALL_COUNTRIES


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, widget=SelectWidget(attrs={"searchable": True}))
        phone_number = forms.CharField(
            label=_("mGage Phone Number"),
            help_text=_("The phone number associated to this channel to send/receive messages."),
            required=True,
        )

        def clean_phone_number(self):
            phone = self.cleaned_data.get("phone_number")

            # short code should not be formatted
            if len(phone) <= 6:
                return phone

            phone = phonenumbers.parse(phone, self.cleaned_data["country"])
            return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)

        def clean(self):
            org = self.request.user.get_org()
            cleaned_data = super().clean()

            phone_number = cleaned_data.get("phone_number", "")
            if not phone_number:
                raise forms.ValidationError(_("Please enter a valid phone number"))

            channel = Channel.objects.filter(
                address=phone_number,
                org=org,
                is_active=True,
                channel_type=self.channel_type.code,
            )
            if channel:
                raise forms.ValidationError(_("A mGage channel with this phone number already exists."))

            return cleaned_data

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()

        self.object = Channel.create(
            org,
            self.request.user,
            self.form.cleaned_data.get("country", None),
            self.channel_type,
            name=self.form.cleaned_data.get("phone_number", ""),
            address=self.form.cleaned_data.get("phone_number", ""),
        )
        return super().form_valid(form)
