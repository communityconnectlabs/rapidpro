# Generated by Django 2.2.10 on 2021-08-19 02:41

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0130_auto_20210420_1325"),
    ]

    operations = [
        migrations.AddField(
            model_name="contactimport",
            name="num_duplicates",
            field=models.IntegerField(default=0),
        ),
    ]
