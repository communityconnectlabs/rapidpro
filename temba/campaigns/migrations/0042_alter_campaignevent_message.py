# Generated by Django 3.2.16 on 2023-01-12 01:44

from django.db import migrations
import temba.utils.models


class Migration(migrations.Migration):

    dependencies = [
        ('campaigns', '0041_merge_20220223_1705'),
    ]

    operations = [
        migrations.AlterField(
            model_name='campaignevent',
            name='message',
            field=temba.utils.models.TranslatableField(max_length=2000, null=True),
        ),
    ]
