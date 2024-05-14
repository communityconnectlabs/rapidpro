# Generated by Django 4.0.3 on 2022-04-14 18:57

from django.db import migrations
from django.db.models import Q


def populate_field_name_and_is_system(apps, schema_editor):  # pragma: no cover
    ContactField = apps.get_model("contacts", "ContactField")

    num_updated = 0
    for field in ContactField.all_fields.filter(Q(name=None) | Q(is_system=None)):
        field.name = field.label
        field.is_system = field.field_type == "S"
        field.save(update_fields=("name", "is_system"))
        num_updated += 1

    if num_updated:
        print(f"Updated name and is_system for {num_updated} contact fields")


def reverse(apps, schema_editor):  # pragma: no cover
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("contacts", "0160_contactfield_is_system_contactfield_name"),
    ]

    operations = [migrations.RunPython(populate_field_name_and_is_system, reverse)]
