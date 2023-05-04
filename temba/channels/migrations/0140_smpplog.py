# Generated by Django 4.0.10 on 2023-05-04 21:11

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("msgs", "0171_alter_broadcast_status"),
        ("channels", "0139_merge_0136_merge_20221101_1805_0138_squashed"),
    ]

    operations = [
        migrations.CreateModel(
            name="SMPPLog",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("W", "Wired"),
                            ("U", "En Route"),
                            ("S", "Sent"),
                            ("D", "Delivered"),
                            ("H", "Handled"),
                            ("E", "Error"),
                            ("F", "Failed"),
                        ],
                        db_index=True,
                        default="W",
                        max_length=1,
                    ),
                ),
                ("created_on", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "channel",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="smpp_channel_logs",
                        to="channels.channel",
                    ),
                ),
                (
                    "msg",
                    models.ForeignKey(
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="smpp_msg_logs",
                        to="msgs.msg",
                    ),
                ),
            ],
        ),
    ]
