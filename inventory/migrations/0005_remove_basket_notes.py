from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0004_importbatch_import_type'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='basket',
            name='notes',
        ),
    ]
