# Generated by Django 2.2.10 on 2021-07-29 22:12

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="report",
            name="org",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to="orgs.Org"),
        ),
    ]
