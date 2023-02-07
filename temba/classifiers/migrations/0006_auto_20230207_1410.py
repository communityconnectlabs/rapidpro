# Generated by Django 3.2.16 on 2023-02-07 14:10

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('classifiers', '0005_classifiertrainingtask'),
    ]

    operations = [
        migrations.AddField(
            model_name='classifiertrainingtask',
            name='created_by',
            field=models.ForeignKey(default=1, help_text='The user which originally created this item', on_delete=django.db.models.deletion.PROTECT, related_name='classifiers_classifiertrainingtask_creations', to='auth.user'),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='classifiertrainingtask',
            name='is_active',
            field=models.BooleanField(default=True, help_text='Whether this item is active, use this instead of deleting'),
        ),
        migrations.AddField(
            model_name='classifiertrainingtask',
            name='modified_by',
            field=models.ForeignKey(default=1, help_text='The user which last modified this item', on_delete=django.db.models.deletion.PROTECT, related_name='classifiers_classifiertrainingtask_modifications', to='auth.user'),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='classifiertrainingtask',
            name='created_on',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was originally created'),
        ),
        migrations.AlterField(
            model_name='classifiertrainingtask',
            name='modified_on',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, editable=False, help_text='When this item was last modified'),
        ),
    ]
