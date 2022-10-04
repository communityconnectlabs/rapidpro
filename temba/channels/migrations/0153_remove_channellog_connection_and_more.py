# Generated by Django 4.0.7 on 2022-10-04 15:27

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("channels", "0152_remove_channelconnection_channelconnection_to_retry"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="channellog",
            name="connection",
        ),
        migrations.AlterField(
            model_name="channellog",
            name="log_type",
            field=models.CharField(
                choices=[
                    ("unknown", "Other Event"),
                    ("msg_send", "Message Send"),
                    ("msg_status", "Message Status"),
                    ("msg_receive", "Message Receive"),
                    ("event_receive", "Event Receive"),
                    ("ivr_start", "IVR Start"),
                    ("ivr_incoming", "IVR Incoming"),
                    ("ivr_callback", "IVR Callback"),
                    ("ivr_status", "IVR Status"),
                    ("ivr_hangup", "IVR Hangup"),
                    ("attachment_fetch", "Attachment Fetch"),
                    ("token_refresh", "Token Refresh"),
                    ("page_subscribe", "Page Subscribe"),
                ],
                max_length=16,
            ),
        ),
    ]
