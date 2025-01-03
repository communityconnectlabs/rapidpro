# Generated by Django 4.0.10 on 2024-12-09 12:05

import django.contrib.postgres.indexes
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0281_alter_exportflowimagestask_created_by_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="flowrun",
            index=django.contrib.postgres.indexes.GinIndex(fields=["events"], name="flows_flowrun_events_idx"),
        ),
    ]
