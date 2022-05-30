import json

from django import forms
from django.core.validators import FileExtensionValidator
from django.utils.translation import ugettext_lazy as _

from temba.classifiers.models import Classifier
from temba.classifiers.types.dialogflow.client import Client
from temba.classifiers.views import BaseConnectView


class ConnectView(BaseConnectView):
    class Form(forms.Form):
        CONFIG_PROJECT_ID = "project_id"
        CONFIG_PRIVATE_KEY = "private_key"
        CONFIG_CLIENT_EMAIL = "client_email"

        file = forms.FileField(validators=[FileExtensionValidator(allowed_extensions=("json",))])

        @classmethod
        def read_uploaded_file(cls, uploaded_file):
            return json.loads(uploaded_file.read())

        def clean(self):
            from temba.classifiers.types.dialogflow import DialogflowType

            cleaned = super().clean()
            # only continue if base validation passed
            if not self.is_valid():
                return cleaned
            json_data = self.read_uploaded_file(cleaned.get("file"))

            if not json_data.get(self.CONFIG_PROJECT_ID) or not json_data.get(self.CONFIG_PRIVATE_KEY):
                raise forms.ValidationError(_("invalid json google service account file"))

            config_search = dict()
            config_search[self.CONFIG_PROJECT_ID] = json_data[self.CONFIG_PROJECT_ID]
            exist = Classifier.objects.filter(
                classifier_type=DialogflowType.slug, config__contains=config_search, is_active=True
            ).first()

            if exist:
                raise forms.ValidationError(_("service account credentials already exist"))
            cleaned["config"] = json_data
            return cleaned

    form_class = Form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["override_form"] = True
        return context

    def form_valid(self, form):
        from temba.classifiers.types.dialogflow import DialogflowType

        config = form.cleaned_data.get("config")
        project_id = config[self.form_class.CONFIG_PROJECT_ID]
        try:
            client = Client(config)
            agent = client.get_agent()
            name = agent.display_name
        except Exception as e:
            print(e)
            name = project_id

        self.object = Classifier.create(self.org, self.request.user, DialogflowType.slug, name, config)

        return super().form_valid(form)
