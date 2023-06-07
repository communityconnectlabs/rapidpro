import csv
import io
from functools import reduce

import pandas as pd
from smartmin.views import SmartCRUDL, SmartFormView, SmartReadView, SmartTemplateView, SmartUpdateView

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.http import HttpResponseRedirect, JsonResponse
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.orgs.views import DependencyDeleteModal, MenuMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils import languages
from temba.utils.fields import CheckboxWidget, SelectMultipleWidget
from temba.utils.gsm7 import calculate_num_segments, is_gsm7, replace_accented_chars
from temba.utils.languages import alpha3_to_alpha2
from temba.utils.views import ComponentFormMixin, SpaMixin

from .models import Classifier, ClassifierDuplicatesCheckTask, ClassifierTrainingTask


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
    actions = ("read", "connect", "delete", "sync", "menu", "train", "check_duplicates")

    class Menu(MenuMixin, OrgPermsMixin, SmartTemplateView):
        def derive_menu(self):
            org = self.request.user.get_org()

            menu = []
            if self.has_org_perm("classifiers.classifier_read"):
                classifiers = Classifier.objects.filter(org=org, is_active=True).order_by("-created_on")
                for classifier in classifiers:
                    menu.append(
                        self.create_menu_item(
                            menu_id=classifier.uuid,
                            name=classifier.name,
                            href=reverse("classifiers.classifier_read", args=[classifier.uuid]),
                            icon=classifier.get_type().icon.replace("icon-", ""),
                        )
                    )

            menu.append(
                {
                    "id": "connect",
                    "href": reverse("classifiers.classifier_connect"),
                    "name": _("Add Classifier"),
                }
            )

            return menu

    class Delete(DependencyDeleteModal):
        cancel_url = "uuid@classifiers.classifier_read"
        success_url = "@orgs.org_home"
        success_message = _("Your classifier has been deleted.")

    class Read(SpaMixin, OrgObjPermsMixin, SmartReadView):
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
                        destructive=True,
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

    class Connect(SpaMixin, OrgPermsMixin, SmartTemplateView):
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["classifier_types"] = Classifier.get_types()
            return context

    class Train(SpaMixin, OrgObjPermsMixin, SmartUpdateView):
        slug_url_kwarg = "uuid"

        class Form(forms.Form):
            file = forms.FileField(validators=[FileExtensionValidator(allowed_extensions=("csv",))])
            language_list = forms.MultipleChoiceField(
                choices=languages.NAMES.items(),
                required=True,
                widget=SelectMultipleWidget(attrs={"searchable": True, "placeholder": "Select Languages"}),
            )
            ignore_errors = forms.BooleanField(
                widget=CheckboxWidget(attrs={"widget_only": True}),
                required=False,
                label=_("Check this box if you want to ignore errors on the file"),
                help_text=None,
            )

        form_class = Form

        def get(self, request, *args, **kwargs):
            report_type = request.GET.get("report-type")
            if report_type == "progress":
                task = ClassifierTrainingTask.get_active_task(self.get_object())
                return JsonResponse(self.get_report(task))

            if report_type == "previous":
                task = ClassifierTrainingTask.get_last_task(self.get_object())
                return JsonResponse(self.get_report(task))

            return super().get(request, *args, **kwargs)

        @classmethod
        def get_report(cls, task):
            report = {}
            if task:
                report = dict(
                    total=task.total_intents,
                    pushed=task.start_index,
                    messages=task.messages,
                    status=task.status,
                    modified=task.modified_on,
                )

            return report

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            del kwargs["instance"]
            return kwargs

        def get_context_data(self, **kwargs):
            obj = self.get_object()
            has_upload_task = ClassifierTrainingTask.has_task(obj)
            context = super().get_context_data(**kwargs)
            context["has_upload_task"] = has_upload_task
            if not has_upload_task:
                task = ClassifierTrainingTask.get_last_task(obj)
                context["has_prev_task"] = task is not None

            context["redirect_success"] = reverse("classifiers.classifier_read", args=[obj.uuid])
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

        @classmethod
        def check_file(cls, file, langs):
            csvreader = csv.DictReader(file)
            error_lines = []
            lang_fields = []

            for lang in langs:
                lang_fields.append(f"Question{str(lang).upper()}")
                lang_fields.append(f"Answer{str(lang).upper()}")

            for idx, row in enumerate(csvreader, start=1):
                invalid_dict = {k: v for k, v in row.items() if k in lang_fields and (v == "" or v == "#N/A")}
                if invalid_dict:
                    error_lines.append(str(idx + 1))  # excluding the file header

            return error_lines

        def post(self, *args, **kwargs):
            message = {}
            status = 200

            file = self.request.FILES.get("file")
            langs = self.request.POST.getlist("language_list")
            ignore_errors = self.request.POST.get("ignore_errors")
            submit_type = self.request.POST.get("submit_type", "S")  # S - normal submit, R - replace, C - check accent

            def get_lang_headers(x):
                from temba.classifiers.types.dialogflow.train_bot import TrainingClient

                headers_data = TrainingClient.get_language_headers(x)
                return [headers_data["training_phrase"], headers_data["answer"]]

            def is_replacement_required(x):
                return not is_gsm7(x) and calculate_num_segments(x) > 1

            form_errors = []

            try:
                if submit_type != "S":
                    df = pd.read_csv(file)
                    df.columns = df.columns.str.lower()
                    headers = reduce(lambda v, i: v + i, map(get_lang_headers, self.convert_langs(langs)))
                    replaced, removed = set(), set()
                    for (column, items) in df[headers].items():
                        for index, item in enumerate(items):
                            if is_replacement_required(item):
                                if submit_type == "C":
                                    replaced_data = replace_accented_chars(item)
                                    replaced = replaced.union(replaced_data["replaced"])
                                    removed = removed.union(replaced_data["removed"])
                                elif submit_type == "R":
                                    df.loc[index, [column]] = replace_accented_chars(item)["updated"]
                    if submit_type == "C" and any(replaced | removed):
                        message["check_result"] = render_to_string(
                            "classifiers/replacements_message.haml", {"accent_chars": ", ".join(replaced | removed)}
                        )
                        return JsonResponse(message, status=202)
                    file = io.StringIO()
                    df.to_csv(file, index=False)
                    file.seek(0)
                elif file:
                    file_buf = io.StringIO()
                    file_buf.write(file.read().decode("utf-8"))
                    file = file_buf
                    file.seek(0)
            except KeyError:
                form_errors.append(
                    "Please check whether the language list of the form is matching with the file header"
                )

            if file and langs and not form_errors:
                raw_data = file.read().splitlines()

                if not ignore_errors:
                    errors = self.check_file(raw_data, langs)
                    if errors:
                        status = 400
                        message["file"] = (
                            f"Please, check your file on the lines {', '.join(errors)}, "
                            f"they seems to be empty or invalid."
                        )
                        return JsonResponse(message, status=status)

                obj = self.get_object()
                ClassifierTrainingTask.create(
                    training_doc=raw_data, classifier=obj, languages=self.convert_langs(langs), user=self.request.user
                )
                messages.success(
                    self.request,
                    f"This will take sometime. We will e-mail you at {self.request.user.username} when it is complete.",
                )
            else:
                status = 400
                if not file:
                    message["file"] = "file is required"
                if not langs:
                    message["language_list"] = "kindly select at least one language"
                if form_errors:
                    message["file"] = ",".join(form_errors)

            return JsonResponse(message, status=status)

    class CheckDuplicates(SpaMixin, OrgPermsMixin, SmartFormView):
        class Form(forms.Form):
            class ColumnsField(forms.MultipleChoiceField):
                def validate(self, value):
                    pass

            def __init__(self, *args, columns=None, **kwargs):
                super().__init__(*args, **kwargs)
                self.fields["columns"].choices = columns or []

            file = forms.FileField(
                validators=[FileExtensionValidator(allowed_extensions=("csv",))],
                required=False,
            )
            task = forms.ModelChoiceField(
                ClassifierDuplicatesCheckTask.objects.all(),
                blank=True,
                required=False,
            )
            columns = ColumnsField(
                required=False,
                widget=SelectMultipleWidget(attrs={"searchable": True, "placeholder": "Select Fields"}),
            )

            def clean_columns(self):
                return self.cleaned_data["columns"]

            def clean(self):
                cleaned_data = super().clean()
                if not any([cleaned_data.get("file"), cleaned_data.get("task")]):
                    self.add_error("file", "This field is required.")

                return cleaned_data

        permission = "classifiers.classifier_train"
        form_class = Form

        def as_json(self, context):
            return context

        def get_success_url(self):
            return reverse("orgs.org_home")

        def form_valid(self, form):
            context = self.get_context_data()
            context["form"] = form

            file_name = form.cleaned_data.get("file")
            if file_name:
                file = self.request.FILES.get("file")
                data = pd.read_csv(file)
                available_columns = list(data.columns)
                task = ClassifierDuplicatesCheckTask.objects.create(
                    origin_file=file,
                    created_by=self.request.user,
                    modified_by=self.request.user,
                )
                form.fields["columns"].choices = ((c, c) for c in available_columns)
                context["file_uploaded"] = True
                context["task"] = task
            else:
                task: ClassifierDuplicatesCheckTask = form.cleaned_data.get("task")
                if task:
                    columns = form.cleaned_data.get("columns", [])
                    if columns:
                        task.metadata["selected_fields"] = columns
                        task.save(update_fields=["metadata"])
                        task.start()
                        messages.info(
                            self.request,
                            _("We are processing your file. We will e-mail you at %s when it is ready.")
                            % self.request.user.username,
                        )
                        return HttpResponseRedirect(self.get_success_url())

                    # render the same page with error message
                    context["task"] = task
                    context["file_uploaded"] = True
                    form.add_error("columns", ValidationError(_("This field is required.")))
                    file = task.origin_file.path
                    data = pd.read_csv(file)
                    available_columns = list(data.columns)
                    form.fields["columns"].choices = ((c, c) for c in available_columns)
            return self.render_to_response(context)
