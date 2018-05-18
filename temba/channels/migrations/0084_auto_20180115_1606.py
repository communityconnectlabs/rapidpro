# Generated by Django 1.11.6 on 2018-01-15 16:06

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0083_use_secret_config'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channelevent',
            name='event_type',
            field=models.CharField(choices=[('unknown', 'Unknown Call Type'), ('mt_call', 'Outgoing Call'), ('mt_miss', 'Missed Outgoing Call'), ('mo_call', 'Incoming Call'), ('mo_miss', 'Missed Incoming Call'), ('stop_contact', 'Stop Contact'), ('new_conversation', 'New Conversation'), ('referral', 'Referral'), ('follow', 'Follow')], help_text='The type of event', max_length=16, verbose_name='Event Type'),
        ),
    ]
