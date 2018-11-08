# -*- coding: utf-8 -*-
# Generated by Django 1.11.6 on 2018-11-07 21:40
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0131_flowimage'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='flowimage',
            name='path',
        ),
        migrations.AddField(
            model_name='flowimage',
            name='url',
            field=models.CharField(default=' ', help_text='Image URL', max_length=255),
            preserve_default=False,
        ),
    ]
