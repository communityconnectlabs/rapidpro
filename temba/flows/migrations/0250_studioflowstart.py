# Generated by Django 2.2.24 on 2022-02-08 15:46

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import temba.utils.json
import temba.utils.models
import temba.utils.uuid


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0129_auto_20211103_1310"),
        ("orgs", "0081_merge_20210517_1335"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contacts", "0134_merge_20211013_2232"),
        ("flows", "0249_merge_20210517_1335"),
    ]

    operations = [
        migrations.CreateModel(
            name="StudioFlowStart",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uuid", models.UUIDField(default=temba.utils.uuid.uuid4, unique=True)),
                ("flow_sid", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[("P", "Pending"), ("S", "Starting"), ("C", "Complete"), ("F", "Failed")],
                        default="P",
                        max_length=1,
                    ),
                ),
                ("metadata", temba.utils.models.JSONField(default=dict, encoder=temba.utils.json.TembaEncoder)),
                ("created_on", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                ("modified_on", models.DateTimeField(default=django.utils.timezone.now, editable=False)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="dependent_studio_flows",
                        to="channels.Channel",
                    ),
                ),
                ("contacts", models.ManyToManyField(to="contacts.Contact")),
                (
                    "created_by",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="studio_flow_starts",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                ("groups", models.ManyToManyField(to="contacts.ContactGroup")),
                (
                    "org",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="studio_flow_starts", to="orgs.Org"
                    ),
                ),
            ],
        ),
    ]
