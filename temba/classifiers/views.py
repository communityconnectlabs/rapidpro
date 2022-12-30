from django.core.validators import FileExtensionValidator
from django import forms
from smartmin.views import SmartCRUDL, SmartFormView, SmartReadView, SmartTemplateView, SmartUpdateView

from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.orgs.views import DependencyDeleteModal, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.views import ComponentFormMixin
from temba.utils import languages
from temba.utils.fields import SelectMultipleWidget
from temba.utils.languages import alpha3_to_alpha2

from .models import Classifier


class BaseConnectView(ComponentFormMixin, OrgPermsMixin, SmartFormView):
    permission = "classifiers.classifier_connect"
    classifier_type = None

    def __init__(self, classifier_type):
        self.classifier_type = classifier_type
        super().__init__()

    def get_template_names(self):
        return (
            "classifiers/types/%s/connect.html" % self.classifier_type.slug,
            "classifiers/classifier_connect_form.html",
        )

    def derive_title(self):
        return _("Connect") + " " + self.classifier_type.name

    def get_success_url(self):
        return reverse("classifiers.classifier_read", args=[self.object.uuid])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_blurb"] = self.classifier_type.get_form_blurb()
        return context


class ClassifierCRUDL(SmartCRUDL):
    model = Classifier
    actions = ("read", "connect", "delete", "sync", "train")

    class Delete(DependencyDeleteModal):
        cancel_url = "uuid@classifiers.classifier_read"
        success_url = "@orgs.org_home"
        success_message = _("Your classifier has been deleted.")

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        exclude = ("id", "is_active", "created_by", "modified_by", "modified_on")

        def get_gear_links(self):
            links = [dict(title=_("Log"), href=reverse("request_logs.httplog_classifier", args=[self.object.uuid]))]

            if self.has_org_perm("classifiers.classifier_sync"):
                links.append(
                    dict(
                        title=_("Sync"),
                        style="btn-secondary",
                        posterize=True,
                        href=reverse("classifiers.classifier_sync", args=[self.object.id]),
                    )
                )
            if self.has_org_perm("classifiers.classifier_delete"):
                links.append(
                    dict(
                        id="ticketer-delete",
                        title=_("Delete"),
                        modax=_("Delete Classifier"),
                        href=reverse("classifiers.classifier_delete", args=[self.object.uuid]),
                    )
                )

            if self.has_org_perm("classifiers.classifier_train"):
                links.append(
                    dict(
                        id="bot-training",
                        title=_("Training"),
                        modax=_("Train Classifier"),
                        href=reverse("classifiers.classifier_train", args=[self.object.uuid]),
                    )
                )

            return links

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            return queryset.filter(org=self.request.user.get_org(), is_active=True)

    class Sync(OrgObjPermsMixin, SmartUpdateView):
        fields = ()
        success_url = "uuid@classifiers.classifier_read"
        success_message = ""

        def post(self, *args, **kwargs):
            self.object = self.get_object()

            try:
                self.object.sync()
                messages.info(self.request, _("Your classifier has been synced."))
            except Exception:
                messages.error(self.request, _("Unable to sync classifier. See the log for details."))

            return HttpResponseRedirect(self.get_success_url())

    class Connect(OrgPermsMixin, SmartTemplateView):
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["classifier_types"] = Classifier.get_types()
            return context

    class Train(OrgObjPermsMixin, SmartUpdateView):
        slug_url_kwarg = "uuid"

        class Form(forms.Form):
            file = forms.FileField(validators=[FileExtensionValidator(allowed_extensions=("csv",))])
            languages = forms.MultipleChoiceField(
                choices=languages.NAMES.items(),
                required=True,
                widget=SelectMultipleWidget(attrs={"searchable": True, "placeholder": "Select Languages"}),
            )

        form_class = Form

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            del kwargs["instance"]
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            return context

        @classmethod
        def convert_langs(cls, langs) -> list:
            converted = []
            for lang in langs:
                alpha_2 = alpha3_to_alpha2(lang)
                # correction for Dialogflow chinese language code
                if alpha_2 == "zh":
                    alpha_2 = "zh-cn"
                converted.append(alpha_2)

            return converted

        def post(self, *args, **kwargs):
            from .types.dialogflow.train_bot import TrainingClient

            message = {}
            status = 200

            file = self.request.FILES.get("file")
            langs = self.request.POST.getlist("languages")
            if file and langs:
                raw_data = file.read().decode("utf-8").splitlines()
                obj = self.get_object()
                trainer = TrainingClient(credential=obj.config, csv_data=raw_data, languages=self.convert_langs(langs))
                trainer.train_bot()
                message = trainer.messages
            else:
                status = 400
                if not file:
                    message["file"] = "file is required"
                if not langs:
                    message["languages"] = "kindly select at least one language"

            return JsonResponse(message, status=status)
