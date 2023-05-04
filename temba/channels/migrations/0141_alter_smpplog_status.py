# Generated by Django 4.0.10 on 2023-05-04 20:24

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0140_smpplog'),
    ]

    operations = [
        migrations.AlterField(
            model_name='smpplog',
            name='status',
            field=models.CharField(choices=[('U', 'En Route'), ('S', 'Sent'), ('D', 'Delivered'), ('H', 'Handled'), ('E', 'Error'), ('F', 'Failed')], db_index=True, default='W', max_length=1),
        ),
    ]
