# Generated by Django 2.2.4 on 2019-11-28 21:25

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [("request_logs", "0003_auto_20191122_2139")]

    operations = [
        migrations.AlterField(
            model_name="httplog",
            name="log_type",
            field=models.CharField(
                choices=[
                    ("intents_synced", "Intents Synced"),
                    ("classifier_called", "Classifier Called"),
                    ("airtime_transferred", "Airtime Transferred"),
                    ("whatsapp_templates_synced", "Whatsapp Templates Synced"),
                    ("whatsapp_tokens_synced", "Whatsapp Tokens Synced"),
                    ("whatsapp_contacts_refreshed", "Whatsapp Contacts Refreshed"),
                ],
                max_length=32,
            ),
        )
    ]
