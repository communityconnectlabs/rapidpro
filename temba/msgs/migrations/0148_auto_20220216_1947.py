# Generated by Django 2.2.24 on 2022-02-16 19:47

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("msgs", "0147_msg_segments"),
    ]

    operations = [
        migrations.AlterField(
            model_name="systemlabelcount",
            name="label_type",
            field=models.CharField(
                choices=[
                    ("I", "Inbox"),
                    ("W", "Flows"),
                    ("A", "Archived"),
                    ("O", "Outbox"),
                    ("S", "Sent"),
                    ("X", "Failed"),
                    ("E", "Scheduled"),
                    ("C", "Calls"),
                    ("V", "Voice Flows"),
                    ("Z", "Voice Sent"),
                ],
                max_length=1,
            ),
        ),
    ]
