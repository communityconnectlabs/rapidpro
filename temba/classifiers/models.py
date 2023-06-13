import logging
import os
from abc import ABCMeta

import pandas as pd
from smartmin.models import SmartModel

from django.core.files.base import ContentFile
from django.db import models
from django.template import Engine
from django.urls import re_path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.orgs.models import DependencyMixin, Org
from temba.utils import on_transaction_commit
from temba.utils.email import send_template_email
from temba.utils.models import JSONField
from temba.utils.uuid import uuid4

logger = logging.getLogger(__name__)


class ClassifierType(metaclass=ABCMeta):
    """
    ClassifierType is our abstract base type for custom NLU providers. Each provider will
    supply a way of connecting a new classifier as well as a way of getting current intents. The job
    of running classifiers and extracting entities will be done by type specific implementations in
    GoFlow and Mailroom.
    """

    # the verbose name for this classifier type
    name = None

    # the short code for this classifier type (< 16 chars, lowercase)
    slug = None

    # the icon to show for this classifier type
    icon = "icon-channel-external"

    # the blurb to show on the main connect page
    connect_blurb = None

    # the view that handles connection of a new model
    connect_view = None

    # the blurb to show on the connect form page
    connect_blurb = None

    def get_connect_blurb(self):
        """
        Gets the blurb for use on the connect page
        """
        return Engine.get_default().from_string(self.connect_blurb)

    def get_form_blurb(self):
        """
        Gets the blurb for use on the connect page
        """
        return Engine.get_default().from_string(self.form_blurb)

    def get_urls(self):
        """
        Returns all the URLs this classifier exposes to Django, the URL should be relative.
        """
        return [self.get_connect_url()]

    def get_connect_url(self):
        """
        Gets the URL/view configuration for this classifier's connect page
        """
        return re_path(r"^connect", self.connect_view.as_view(classifier_type=self), name="connect")

    def get_active_intents_from_api(self, classifier):
        """
        Should return current set of available intents for the passed in classifier by checking the provider API
        """
        raise NotImplementedError("classifier types must implement get_intents")


class Classifier(SmartModel, DependencyMixin):
    """
    A classifier represents a set of intents and entity extractors. Many providers call
    these "apps".
    """

    # our uuid
    uuid = models.UUIDField(default=uuid4)

    # the type of this classifier
    classifier_type = models.CharField(max_length=16)

    # the friendly name for this classifier
    name = models.CharField(max_length=255)

    # config values for this classifier
    config = JSONField()

    # the org this classifier is part of
    org = models.ForeignKey(Org, related_name="classifiers", on_delete=models.PROTECT)

    @classmethod
    def create(cls, org, user, classifier_type, name, config, sync=True):
        classifier = Classifier.objects.create(
            uuid=uuid4(),
            name=name,
            classifier_type=classifier_type,
            config=config,
            org=org,
            created_by=user,
            modified_by=user,
            created_on=timezone.now(),
            modified_on=timezone.now(),
        )

        # trigger a sync of this classifier's intents
        if sync:
            classifier.async_sync()

        return classifier

    def get_type(self):
        """
        Returns the type instance for this classifier
        """
        from .types import TYPES

        return TYPES[self.classifier_type]

    def active_intents(self):
        """
        Returns the list of active intents on this classifier
        """
        return self.intents.filter(is_active=True).order_by("name")

    def sync(self):
        """
        Refresh intents fetches the current intents from the classifier API and updates
        the DB appropriately to match them, inserting logs for all interactions.
        """
        # get the current intents from the API
        intents = self.get_type().get_active_intents_from_api(self)

        # external ids we have seen
        seen = []

        # for each intent
        for intent in intents:
            assert intent.external_id is not None
            assert intent.name != "" and intent.name is not None

            seen.append(intent.external_id)

            existing = self.intents.filter(external_id=intent.external_id).first()
            if existing:
                # previously existed, reactive it
                if not existing.is_active:
                    existing.is_active = True
                    existing.save(update_fields=["is_active"])

            elif not existing:
                Intent.objects.create(
                    is_active=True,
                    classifier=self,
                    name=intent.name,
                    external_id=intent.external_id,
                    created_on=timezone.now(),
                )

        # deactivate any intent we haven't seen
        self.intents.filter(is_active=True).exclude(external_id__in=seen).update(is_active=False)

    def async_sync(self):
        """
        Triggers a sync of this classifiers intents
        """
        from .tasks import sync_classifier_intents

        on_transaction_commit(lambda: sync_classifier_intents.delay(self.id))

    def release(self, user):
        super().release(user)

        # delete our intents
        self.intents.all().delete()

        self.is_active = False
        self.modified_by = user
        self.save(update_fields=("is_active", "modified_by", "modified_on"))

    @classmethod
    def get_types(cls):
        """
        Returns the possible types available for classifiers
        :return:
        """
        from .types import TYPES

        return TYPES.values()


class Intent(models.Model):
    """
    Intent represents an intent that a classifier can classify to. It is the job of
    model type implementations to sync these periodically for use in flows etc..
    """

    # intents are forever on an org, but they do get marked inactive when no longer around
    is_active = models.BooleanField(default=True)

    # the classifier this intent is tied to
    classifier = models.ForeignKey(Classifier, related_name="intents", on_delete=models.PROTECT)

    # the name of the intent
    name = models.CharField(max_length=255)

    # the external id of the intent, in same cases this is the same as the name but that is provider specific
    external_id = models.CharField(max_length=255)

    # when we first saw / created this intent
    created_on = models.DateTimeField(default=timezone.now)

    class Meta:
        unique_together = (("classifier", "external_id"),)


class ClassifierTrainingTask(SmartModel):
    PENDING = "P"
    IN_PROGRESS = "I"
    RETRY = "R"
    COMPLETED = "C"
    FAILED = "F"

    STATUS = (
        (PENDING, _("Pending")),
        (IN_PROGRESS, _("In Progress")),
        (RETRY, _("Retry")),
        (COMPLETED, _("Completed")),
        (FAILED, _("Failed")),
    )

    classifier = models.ForeignKey(Classifier, on_delete=models.CASCADE)
    training_doc = JSONField(default=list)
    pickled_doc = models.TextField(null=True)
    status = models.CharField(default=PENDING, choices=STATUS, max_length=2)
    messages = JSONField(default=dict)
    languages = JSONField(default=list)
    start_index = models.IntegerField(default=0)
    total_intents = models.PositiveSmallIntegerField(default=0)

    @classmethod
    def create(cls, classifier, training_doc, languages, user):
        created_obj = cls.objects.create(
            training_doc=training_doc, classifier=classifier, languages=languages, created_by=user, modified_by=user
        )
        created_obj.start_training()
        return created_obj

    def start_training(self):
        from .tasks import train_bot

        on_transaction_commit(lambda: train_bot.delay(self.id))

    @classmethod
    def get_active_tasks(cls, classifier):
        active_status = [cls.RETRY, cls.IN_PROGRESS, cls.PENDING]
        return cls.objects.filter(classifier=classifier, status__in=active_status)

    @classmethod
    def get_last_task(cls, classifier):
        return cls.objects.filter(classifier=classifier).order_by("-modified_on").first()

    @classmethod
    def get_active_task(cls, classifier):
        return cls.get_active_tasks(classifier).first()

    @classmethod
    def has_task(cls, classifier):
        tasks = cls.get_active_tasks(classifier).count()
        return tasks > 0

    def trash_docs(self):
        self.training_doc = []
        self.pickled_doc = None

    @classmethod
    def run_task(cls, instance_id):
        from .types.dialogflow.train_bot import TrainingClient

        filter_status = [cls.PENDING, cls.RETRY]

        reschedule_task = False
        training = None
        try:
            training = cls.objects.get(id=instance_id)
        except Exception as e:
            logger.error(e, exc_info=True)

        if training and training.status in filter_status:
            training.status = cls.IN_PROGRESS
            training.save()

            client = TrainingClient(
                credential=training.classifier.config,
                csv_data=training.training_doc,
                languages=training.languages,
                messages=training.messages,
            )

            if not training.pickled_doc:
                client.build_intent_list()
                intent_list = client.intents_requests
            else:
                intent_list = client.intent_str_to_list(training.pickled_doc)

            if training.start_index == 0:
                training.total_intents = len(intent_list)
                training.save()

            index, retry, completed = client.push_to_dialogflow(intent_list, training.start_index)
            training.start_index = index
            training.messages = client.messages
            if not training.pickled_doc:
                training.pickled_doc = client.intent_list_to_str()

            if retry:
                training.status = cls.RETRY
            if completed:
                training.status = cls.COMPLETED
                training.trash_docs()

            if not completed and not retry:
                training.status = cls.FAILED
                training.trash_docs()

            reschedule_task = retry
            training.modified_on = timezone.now()
            training.save()

            if training.status in [cls.COMPLETED, cls.FAILED]:
                send_template_email(
                    training.created_by.username,
                    f"[{training.classifier.org.name}] Classifier training complete",
                    "classifiers/email/training_email",
                    dict(
                        total_created=training.messages.get("created", 0),
                        total_updated=training.messages.get("updated", 0),
                        errors=list(training.messages.get("errors", [])),
                    ),
                    training.classifier.org.get_branding(),
                )

        return reschedule_task


class ClassifierDuplicatesCheckTask(SmartModel):
    PENDING = "P"
    IN_PROGRESS = "I"
    COMPLETED = "C"
    FAILED = "F"

    STATUS = (
        (PENDING, _("Pending")),
        (IN_PROGRESS, _("In Progress")),
        (COMPLETED, _("Completed")),
        (FAILED, _("Failed")),
    )

    origin_file = models.FileField(upload_to="duplicates_check")
    result_file = models.FileField(upload_to="duplicates_check", null=True, blank=True)
    status = models.CharField(default=PENDING, choices=STATUS, max_length=2)
    metadata = models.JSONField(default=dict)

    @property
    def file_name(self):
        return os.path.basename(self.origin_file.path)

    @classmethod
    def create(cls, file, user):
        return cls.objects.create(origin_file=file, created_by=user, modified_by=user)

    def start(self):
        from .tasks import check_duplicates

        check_duplicates.delay(self.id)

    def perform(self):
        self.status = ClassifierDuplicatesCheckTask.IN_PROGRESS
        self.save(update_fields=["status"])

        columns = self.metadata["selected_columns"]
        file = self.origin_file.path
        new_filename = f"{os.path.basename(file).removesuffix('.csv')}_similarity.csv"
        df = pd.read_csv(file)
        df_copy = df.copy()

        try:
            for column in columns:
                column_similarity = f"{column}_Smlr"
                for i, value in df_copy[column].items():
                    similar_rows = []
                    for j, other_value in df_copy[column].items():
                        if i != j and value == other_value:
                            similar_rows.append(j)
                    df_copy.at[i, column_similarity] = ";".join(map(str, similar_rows))

            self.result_file.save(new_filename, ContentFile(df_copy.to_csv(index=False).encode("utf-8")))
            self.status = ClassifierDuplicatesCheckTask.COMPLETED
            self.save(update_fields=["status"])
        except Exception as e:
            logger.error("Similarity search process has failed. %s", str(e))
            self.status = ClassifierDuplicatesCheckTask.FAILED
            self.save(update_fields=["status"])
