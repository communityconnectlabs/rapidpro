# Generated by Django 4.0.6 on 2024-06-11 15:04

from django.db import migrations, models
import temba.utils.uuid


class Migration(migrations.Migration):

    dependencies = [
        ('links', '0008_alter_exportlinkstask_created_by_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='link',
            name='is_system',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='link',
            name='uuid',
            field=models.UUIDField(default=temba.utils.uuid.uuid4, unique=True),
        ),
    ]
