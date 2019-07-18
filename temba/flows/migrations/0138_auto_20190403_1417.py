# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2019-04-03 17:17
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0137_merge_20190403_1417'),
    ]

    operations = [
        migrations.AddField(
            model_name='flow',
            name='launch_status',
            field=models.CharField(choices=[('D', 'Demo'), ('P', 'Production')], help_text='The launch status of this flow', max_length=1, null=True),
        ),
        migrations.AlterField(
            model_name='ruleset',
            name='ruleset_type',
            field=models.CharField(choices=[('wait_message', 'Wait for message'), ('wait_date', 'Wait for date'), ('wait_menu', 'Wait for USSD menu'), ('wait_ussd', 'Wait for USSD message'), ('wait_recording', 'Wait for recording'), ('wait_digit', 'Wait for digit'), ('wait_digits', 'Wait for digits'), ('all_that_apply', 'Wait for all that apply'), ('subflow', 'Subflow'), ('webhook', 'Webhook'), ('resthook', 'Resthook'), ('airtime', 'Transfer Airtime'), ('form_field', 'Split by message form'), ('contact_field', 'Split on contact field'), ('expression', 'Split by expression'), ('random', 'Split Randomly'), ('shorten_url', 'Shorten Trackable Link'), ('lookup', 'Lookup'), ('giftcard', 'Gift Card')], help_text='The type of ruleset', max_length=16, null=True),
        ),
    ]
