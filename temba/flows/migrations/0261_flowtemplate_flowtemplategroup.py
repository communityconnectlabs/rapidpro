# Generated by Django 2.2.27 on 2022-09-06 18:37

import django.contrib.postgres.fields
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import temba.utils.models
import temba.utils.uuid


class Migration(migrations.Migration):

    dependencies = [
        ("orgs", "0089_merge_20220223_1706"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("flows", "0260_merge_20220316_1721"),
    ]

    operations = [
        migrations.CreateModel(
            name="FlowTemplateGroup",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uuid", models.UUIDField(default=temba.utils.uuid.uuid4, unique=True)),
                ("name", models.CharField(max_length=64, unique=True)),
            ],
        ),
        migrations.CreateModel(
            name="FlowTemplate",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uuid", models.UUIDField(default=temba.utils.uuid.uuid4, unique=True)),
                ("name", models.CharField(max_length=64, unique=True)),
                ("document", temba.utils.models.JSONAsTextField(default=dict, help_text="imported flow file")),
                (
                    "tags",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=10, null=True), default=list, null=True, size=None
                    ),
                ),
                ("description", models.TextField(null=True)),
                ("global_view", models.BooleanField(default=False)),
                ("created_on", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("modified_on", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="flow_template",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "group",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="group", to="flows.FlowTemplateGroup"
                    ),
                ),
                ("orgs", models.ManyToManyField(related_name="flow_template", to="orgs.Org")),
            ],
        ),
    ]
