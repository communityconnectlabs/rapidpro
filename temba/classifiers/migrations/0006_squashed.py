# This is a dummy migration which will be implemented in 7.3

from django.conf import settings
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("classifiers", "0005_alter_classifier_created_by_and_more"),
    ]

    operations = []
