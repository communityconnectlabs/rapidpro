# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-04-27 19:27
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0071_auto_20180405_1848'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='salesforce_id',
            field=models.CharField(blank=True, help_text='Salesforce ID related to this contact', max_length=255, null=True, verbose_name='Salesforce ID'),
        ),
    ]
