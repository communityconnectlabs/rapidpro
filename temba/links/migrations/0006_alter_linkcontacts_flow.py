# Generated by Django 3.2.16 on 2022-11-07 11:50

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0262_merge_20221101_1805"),
        ("links", "0005_linkcontacts_flow"),
    ]

    operations = [
        migrations.AlterField(
            model_name="linkcontacts",
            name="flow",
            field=models.ForeignKey(
                help_text="The flow related to this link's click",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="flow_links",
                to="flows.flow",
            ),
        ),
    ]
