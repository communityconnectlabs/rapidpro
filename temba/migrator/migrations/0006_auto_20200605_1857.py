# Generated by Django 2.2.4 on 2020-06-05 18:57

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('migrator', '0005_auto_20200604_2125'),
    ]

    operations = [
        migrations.AlterField(
            model_name='migrationassociation',
            name='model',
            field=models.CharField(choices=[('campaigns_campaign', 'campaigns_campaign'), ('campaigns_campaignevent', 'campaigns_campaignevent'), ('channels_channel', 'channels_channel'), ('contacts_contact', 'contacts_contact'), ('contacts_contacturn', 'contacts_contacturn'), ('contacts_contactgroup', 'contacts_contactgroup'), ('contacts_contactfield', 'contacts_contactfield'), ('msgs_msg', 'msgs_msg'), ('msgs_label', 'msgs_label'), ('msgs_broadcast', 'msgs_broadcast'), ('flows_flow', 'flows_flow'), ('flows_flowlabel', 'flows_flowlabel'), ('flows_flowrun', 'flows_flowrun'), ('flows_flowstart', 'flows_flowstart'), ('links_link', 'links_link'), ('schedules_schedule', 'schedules_schedule'), ('orgs_org', 'orgs_org'), ('orgs_topups', 'orgs_topups'), ('orgs_language', 'orgs_language'), ('triggers_trigger', 'triggers_trigger')], max_length=255, verbose_name='Model related to the ID'),
        ),
    ]
