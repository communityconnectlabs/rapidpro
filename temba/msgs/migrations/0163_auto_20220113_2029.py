# Generated by Django 3.2.9 on 2022-01-13 20:29

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("flows", "0264_populate_session_wait_expires"),
        ("msgs", "0162_alter_msg_failed_reason"),
    ]

    operations = [
        migrations.AddField(
            model_name="msg",
            name="flow",
            field=models.ForeignKey(
                db_index=False, null=True, on_delete=django.db.models.deletion.PROTECT, to="flows.flow"
            ),
        ),
        migrations.AlterField(
            model_name="msg",
            name="broadcast",
            field=models.ForeignKey(
                null=True, on_delete=django.db.models.deletion.PROTECT, related_name="msgs", to="msgs.broadcast"
            ),
        ),
    ]
