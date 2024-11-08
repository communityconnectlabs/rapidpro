import logging
from datetime import timedelta

from django.utils.timezone import now

from celery import shared_task

from temba.utils.celery import nonoverlapping_task

from .models import Classifier, ClassifierDuplicatesCheckTask, ClassifierTrainingTask

logger = logging.getLogger(__name__)


@nonoverlapping_task(track_started=True, name="sync_classifier_intents", lock_timeout=300)
def sync_classifier_intents(id=None):
    classifiers = Classifier.objects.filter(is_active=True)
    if id:
        classifiers = classifiers.filter(id=id)

    # for each classifier, synchronize to update the intents etc
    for classifier in classifiers:
        try:
            classifier.sync()
        except Exception as e:
            logger.error("error getting intents for classifier", e)


@shared_task(name="train_dialogflow", bind=True)
def train_bot(self, instance_id):
    reschedule_task = ClassifierTrainingTask.run_task(instance_id)
    if reschedule_task:
        # retry after a minute and 10 seconds later
        next_retry = now() + timedelta(seconds=70)
        self.apply_async((instance_id,), eta=next_retry)


@shared_task(name="check_duplicates")
def check_duplicates(instance_id):
    task = ClassifierDuplicatesCheckTask.objects.filter(id=instance_id).first()
    if task:
        task.perform()
