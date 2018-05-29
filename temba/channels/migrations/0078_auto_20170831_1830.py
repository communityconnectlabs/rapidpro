# Generated by Django 1.11.2 on 2017-08-31 18:30

from django.db import migrations, models
from django.db.models import F, TextField, Value
from django.db.models.functions import Concat

from temba.utils import chunk_list

SQL_UPDATE_CHANNELEVENT = """
-- Trigger procedure to update system labels on channel event changes
CREATE OR REPLACE FUNCTION temba_channelevent_on_change() RETURNS TRIGGER AS $$
BEGIN
  -- new event inserted
  IF TG_OP = 'INSERT' THEN
    -- don't update anything for a non-call event or test call
    IF NOT temba_channelevent_is_call(NEW) OR temba_contact_is_test(NEW.contact_id) THEN
      RETURN NULL;
    END IF;

    PERFORM temba_insert_system_label(NEW.org_id, 'C', 1);

  -- existing call updated
  ELSIF TG_OP = 'UPDATE' THEN
    -- don't update anything for a non-call event or test call
    IF NOT temba_channelevent_is_call(NEW) OR temba_contact_is_test(NEW.contact_id) THEN
      RETURN NULL;
    END IF;

  -- existing call deleted
  ELSIF TG_OP = 'DELETE' THEN
    -- don't update anything for a non-call event or test call
    IF NOT temba_channelevent_is_call(OLD) OR temba_contact_is_test(OLD.contact_id) THEN
      RETURN NULL;
    END IF;

    PERFORM temba_insert_system_label(OLD.org_id, 'C', -1);

  -- all calls deleted
  ELSIF TG_OP = 'TRUNCATE' THEN
    PERFORM temba_reset_system_labels('{"C"}');

  END IF;

  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE FUNCTION temba_channelevent_is_call(_event channels_channelevent) RETURNS BOOLEAN AS $$
BEGIN
  RETURN _event.event_type IN ('mo_call', 'mo_miss', 'mt_call', 'mt_miss');
END;
$$ LANGUAGE plpgsql;
"""


def delete_inactive_channelevents(apps, schema_editor):
    ChannelEvent = apps.get_model("channels", "ChannelEvent")

    # delete all channel events that are inactive, we don't care to keep those around
    ids = ChannelEvent.objects.filter(is_active=False).values_list("id", flat=True)
    if ids:
        print("Found %d channel events to delete" % len(ids))

    count = 0
    for chunk in chunk_list(ids, 1000):
        ChannelEvent.objects.filter(id__in=chunk).delete()
        count += len(chunk)
        print("Deleted %d" % count)


def migrate_duration_extra(apps, schema_editor):
    ChannelEvent = apps.get_model("channels", "ChannelEvent")

    # find all events with a duration and convert them to extra
    ids = ChannelEvent.objects.filter(duration__gte=0).values_list("id", flat=True)
    if ids:
        print("Found %d channel events to set extra on" % len(ids))

    count = 0
    for chunk in chunk_list(ids, 250):
        ChannelEvent.objects.filter(id__in=chunk).update(
            extra=Concat(Value('{"duration":'), F("duration"), Value("}"), output_field=TextField())
        )
        count += len(chunk)
        print("Updated %d" % count)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    atomic = False

    atomic = False

    dependencies = [("channels", "0077_auto_20170824_1555"), ("msgs", "0076_install_triggers")]

    operations = [
        migrations.RunPython(delete_inactive_channelevents, noop),
        migrations.AlterField(
            model_name="channelevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("unknown", "Unknown Call Type"),
                    ("mt_call", "Outgoing Call"),
                    ("mt_miss", "Missed Outgoing Call"),
                    ("mo_call", "Incoming Call"),
                    ("mo_miss", "Missed Incoming Call"),
                    ("new_conversation", "New Conversation"),
                    ("referral", "Referral"),
                    ("follow", "Follow"),
                ],
                help_text="The type of event",
                max_length=16,
                verbose_name="Event Type",
            ),
        ),
        migrations.RemoveField(model_name="channelevent", name="is_active"),
        migrations.RenameField(model_name="channelevent", old_name="time", new_name="occurred_on"),
        migrations.AlterField(
            model_name="channelevent",
            name="occurred_on",
            field=models.DateTimeField(help_text="When this event took place", verbose_name="Occurred On"),
        ),
        migrations.AddField(
            model_name="channelevent",
            name="extra",
            field=models.TextField(
                help_text="Any extra properties on this event as JSON", null=True, verbose_name="Extra"
            ),
        ),
        migrations.RunSQL(SQL_UPDATE_CHANNELEVENT, ""),
        migrations.RunPython(migrate_duration_extra, noop),
        migrations.RemoveField(model_name="channelevent", name="duration"),
        migrations.RunSQL(SQL_UPDATE_CHANNELEVENT, ""),
    ]
