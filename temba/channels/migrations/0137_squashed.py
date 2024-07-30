# Generated by Django 4.0.3 on 2022-03-10 17:56

import django_countries.fields

import django.contrib.postgres.fields
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models

import temba.channels.models
import temba.orgs.models
import temba.utils.models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Alert",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "is_active",
                    models.BooleanField(
                        default=True, help_text="Whether this item is active, use this instead of deleting"
                    ),
                ),
                (
                    "created_on",
                    models.DateTimeField(
                        blank=True,
                        default=django.utils.timezone.now,
                        editable=False,
                        help_text="When this item was originally created",
                    ),
                ),
                (
                    "modified_on",
                    models.DateTimeField(
                        blank=True,
                        default=django.utils.timezone.now,
                        editable=False,
                        help_text="When this item was last modified",
                    ),
                ),
                (
                    "alert_type",
                    models.CharField(
                        choices=[("P", "Power"), ("D", "Disconnected"), ("S", "SMS")],
                        help_text="The type of alert the channel is sending",
                        max_length=1,
                        verbose_name="Alert Type",
                    ),
                ),
                ("ended_on", models.DateTimeField(blank=True, null=True, verbose_name="Ended On")),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="Channel",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "is_active",
                    models.BooleanField(
                        default=True, help_text="Whether this item is active, use this instead of deleting"
                    ),
                ),
                (
                    "created_on",
                    models.DateTimeField(
                        blank=True,
                        default=django.utils.timezone.now,
                        editable=False,
                        help_text="When this item was originally created",
                    ),
                ),
                (
                    "modified_on",
                    models.DateTimeField(
                        blank=True,
                        default=django.utils.timezone.now,
                        editable=False,
                        help_text="When this item was last modified",
                    ),
                ),
                (
                    "uuid",
                    models.CharField(
                        db_index=True,
                        default=temba.utils.models.generate_uuid,
                        help_text="The unique identifier for this object",
                        max_length=36,
                        unique=True,
                        verbose_name="Unique Identifier",
                    ),
                ),
                ("channel_type", models.CharField(max_length=3)),
                ("name", models.CharField(max_length=64, null=True)),
                (
                    "address",
                    models.CharField(
                        blank=True,
                        help_text="Address with which this channel communicates",
                        max_length=255,
                        null=True,
                        verbose_name="Address",
                    ),
                ),
                (
                    "country",
                    django_countries.fields.CountryField(
                        blank=True,
                        help_text="Country which this channel is for",
                        max_length=2,
                        null=True,
                        verbose_name="Country",
                    ),
                ),
                (
                    "claim_code",
                    models.CharField(
                        blank=True,
                        help_text="The token the user will us to claim this channel",
                        max_length=16,
                        null=True,
                        unique=True,
                        verbose_name="Claim Code",
                    ),
                ),
                (
                    "secret",
                    models.CharField(
                        blank=True,
                        help_text="The secret token this channel should use when signing requests",
                        max_length=64,
                        null=True,
                        unique=True,
                        verbose_name="Secret",
                    ),
                ),
                (
                    "last_seen",
                    models.DateTimeField(
                        auto_now_add=True,
                        help_text="The last time this channel contacted the server",
                        verbose_name="Last Seen",
                    ),
                ),
                (
                    "device",
                    models.CharField(
                        blank=True,
                        help_text="The type of Android device this channel is running on",
                        max_length=255,
                        null=True,
                        verbose_name="Device",
                    ),
                ),
                (
                    "os",
                    models.CharField(
                        blank=True,
                        help_text="What Android OS version this channel is running on",
                        max_length=255,
                        null=True,
                        verbose_name="OS",
                    ),
                ),
                (
                    "alert_email",
                    models.EmailField(
                        blank=True,
                        help_text="We will send email alerts to this address if experiencing issues sending",
                        max_length=254,
                        null=True,
                        verbose_name="Alert Email",
                    ),
                ),
                ("config", temba.utils.models.JSONAsTextField(default=dict, null=True)),
                (
                    "schemes",
                    django.contrib.postgres.fields.ArrayField(
                        base_field=models.CharField(max_length=16),
                        default=temba.channels.models._get_default_channel_scheme,
                        size=None,
                    ),
                ),
                ("role", models.CharField(default="SR", max_length=4)),
                ("bod", models.TextField(null=True)),
                (
                    "tps",
                    models.IntegerField(
                        help_text="The max number of messages that will be sent per second",
                        null=True,
                        verbose_name="Maximum Transactions per Second",
                    ),
                ),
            ],
            options={
                "ordering": ("-last_seen", "-pk"),
            },
            bases=(models.Model, temba.orgs.models.DependencyMixin),
        ),
        migrations.CreateModel(
            name="ChannelConnection",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("connection_type", models.CharField(choices=[("V", "Voice")], max_length=1)),
                ("direction", models.CharField(choices=[("I", "Incoming"), ("O", "Outgoing")], max_length=1)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("P", "Pending"),
                            ("Q", "Queued"),
                            ("W", "Wired"),
                            ("I", "In Progress"),
                            ("D", "Complete"),
                            ("E", "Errored"),
                            ("F", "Failed"),
                        ],
                        max_length=1,
                    ),
                ),
                (
                    "answered_by",
                    models.CharField(
                        choices=[
                            ("H", "Human"),
                            ("M", "Machine"),
                        ],
                        max_length=1,
                        null=True,
                    ),
                ),
                ("external_id", models.CharField(max_length=255)),
                ("created_on", models.DateTimeField(default=django.utils.timezone.now)),
                ("modified_on", models.DateTimeField(default=django.utils.timezone.now)),
                ("started_on", models.DateTimeField(null=True)),
                ("ended_on", models.DateTimeField(null=True)),
                ("duration", models.IntegerField(null=True)),
                (
                    "error_reason",
                    models.CharField(
                        choices=[("P", "Provider"), ("B", "Busy"), ("N", "No Answer"), ("M", "Answering Machine")],
                        max_length=1,
                        null=True,
                    ),
                ),
                ("error_count", models.IntegerField(default=0)),
                ("next_attempt", models.DateTimeField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name="ChannelCount",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ("is_squashed", models.BooleanField(default=False)),
                (
                    "count_type",
                    models.CharField(
                        choices=[
                            ("IM", "Incoming Message"),
                            ("OM", "Outgoing Message"),
                            ("IV", "Incoming Voice"),
                            ("OV", "Outgoing Voice"),
                            ("LS", "Success Log Record"),
                            ("LE", "Error Log Record"),
                        ],
                        max_length=2,
                    ),
                ),
                ("day", models.DateField(null=True)),
                ("count", models.IntegerField(default=0)),
            ],
        ),
        migrations.CreateModel(
            name="ChannelEvent",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "event_type",
                    models.CharField(
                        choices=[
                            ("unknown", "Unknown Call Type"),
                            ("mt_call", "Outgoing Call"),
                            ("mt_miss", "Missed Outgoing Call"),
                            ("mo_call", "Incoming Call"),
                            ("mo_miss", "Missed Incoming Call"),
                            ("stop_contact", "Stop Contact"),
                            ("new_conversation", "New Conversation"),
                            ("referral", "Referral"),
                            ("welcome_message", "Welcome Message"),
                        ],
                        max_length=16,
                    ),
                ),
                ("extra", temba.utils.models.JSONAsTextField(default=dict, null=True)),
                ("occurred_on", models.DateTimeField()),
                ("created_on", models.DateTimeField(default=django.utils.timezone.now)),
            ],
        ),
        migrations.CreateModel(
            name="SyncEvent",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "is_active",
                    models.BooleanField(
                        default=True, help_text="Whether this item is active, use this instead of deleting"
                    ),
                ),
                (
                    "created_on",
                    models.DateTimeField(
                        blank=True,
                        default=django.utils.timezone.now,
                        editable=False,
                        help_text="When this item was originally created",
                    ),
                ),
                (
                    "modified_on",
                    models.DateTimeField(
                        blank=True,
                        default=django.utils.timezone.now,
                        editable=False,
                        help_text="When this item was last modified",
                    ),
                ),
                (
                    "power_source",
                    models.CharField(
                        choices=[("AC", "A/C"), ("USB", "USB"), ("WIR", "Wireless"), ("BAT", "Battery")], max_length=64
                    ),
                ),
                (
                    "power_status",
                    models.CharField(
                        choices=[
                            ("UNK", "Unknown"),
                            ("CHA", "Charging"),
                            ("DIS", "Discharging"),
                            ("NOT", "Not Charging"),
                            ("FUL", "FUL"),
                        ],
                        default="UNK",
                        max_length=64,
                    ),
                ),
                ("power_level", models.IntegerField()),
                ("network_type", models.CharField(max_length=128)),
                ("lifetime", models.IntegerField(blank=True, default=0, null=True)),
                ("pending_message_count", models.IntegerField(default=0)),
                ("retry_message_count", models.IntegerField(default=0)),
                ("incoming_command_count", models.IntegerField(default=0)),
                ("outgoing_command_count", models.IntegerField(default=0)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="sync_events", to="channels.channel"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        help_text="The user which originally created this item",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="%(app_label)s_%(class)s_creations",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        help_text="The user which last modified this item",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="%(app_label)s_%(class)s_modifications",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "abstract": False,
            },
        ),
        migrations.CreateModel(
            name="ChannelLog",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("description", models.CharField(max_length=255)),
                ("is_error", models.BooleanField(default=False)),
                ("url", models.TextField(null=True)),
                ("method", models.CharField(max_length=16, null=True)),
                ("request", models.TextField(null=True)),
                ("response", models.TextField(null=True)),
                ("response_status", models.IntegerField(null=True)),
                ("created_on", models.DateTimeField(default=django.utils.timezone.now)),
                ("request_time", models.IntegerField(null=True)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT, related_name="logs", to="channels.channel"
                    ),
                ),
                (
                    "connection",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="channel_logs",
                        to="channels.channelconnection",
                    ),
                ),
            ],
        ),
    ]
