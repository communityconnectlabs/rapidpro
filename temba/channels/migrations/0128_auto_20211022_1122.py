# Generated by Django 2.2.24 on 2021-10-22 11:22

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0127_merge_20211013_2232"),
    ]

    operations = [
        migrations.AlterField(
            model_name="channelevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("unknown", "Unknown Call Type"),
                    ("mt_call", "Outgoing Call"),
                    ("mt_miss", "Missed Outgoing Call"),
                    ("mo_call", "Incoming Call"),
                    ("mo_miss", "Missed Incoming Call"),
                    ("stop_contact", "Stop Contact"),
                    ("new_conversation", "New Conversation"),
                    ("stop_conversation", "Stop Conversation"),
                    ("referral", "Referral"),
                    ("welcome_message", "Welcome Message"),
                ],
                max_length=16,
            ),
        ),
    ]
