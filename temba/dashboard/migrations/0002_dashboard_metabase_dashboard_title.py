# Generated by Django 2.2.27 on 2022-11-03 16:21

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="dashboard",
            name="metabase_dashboard_title",
            field=models.CharField(default="", max_length=255, verbose_name="Metabase Dashboard Title"),
            preserve_default=False,
        ),
    ]
