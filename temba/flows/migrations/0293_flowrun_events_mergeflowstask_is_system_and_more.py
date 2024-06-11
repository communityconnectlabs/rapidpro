# Generated by Django 4.0.6 on 2024-06-11 15:04

from django.db import migrations, models
import temba.utils.fields
import temba.utils.uuid


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0292_merge_20240611_1502'),
    ]

    operations = [
        migrations.AddField(
            model_name='mergeflowstask',
            name='is_system',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='mergeflowstask',
            name='name',
            field=models.CharField(default='Merge Flow Test', max_length=64, validators=[temba.utils.fields.NameValidator(64)]),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name='mergeflowstask',
            name='uuid',
            field=models.UUIDField(default=temba.utils.uuid.uuid4, unique=True),
        ),
    ]
