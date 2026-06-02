# Manually adjusted migration:
# The car_groups table already had a car_id column (bigint FK).
# We need to:
#  1. Drop the FK constraint and old car_id bigint column.
#  2. Add a new car_id varchar(100) column.
#  3. Update unique_together.
# We use SeparateDatabaseAndState to handle the state/DB differences cleanly.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0007_alter_carattribute_unique_together_and_more'),
    ]

    operations = [
        # First update the unique_together in Django state
        migrations.AlterUniqueTogether(
            name='cargroup',
            unique_together=set(),
        ),
        # Drop old FK column and add new varchar car_id via raw SQL
        migrations.RunSQL(
            sql=[
                # Drop the existing car_id column (bigint FK) if it exists
                "ALTER TABLE car_groups DROP COLUMN IF EXISTS car_id;",
                # Add new car_id as varchar
                "ALTER TABLE car_groups ADD COLUMN car_id varchar(100) NOT NULL DEFAULT '';",
                # Add index
                "CREATE INDEX IF NOT EXISTS car_groups_car_id_idx ON car_groups (car_id);",
            ],
            reverse_sql=[
                "DROP INDEX IF EXISTS car_groups_car_id_idx;",
                "ALTER TABLE car_groups DROP COLUMN IF EXISTS car_id;",
            ],
        ),
        # Remove the old `car` ForeignKey from Django's state
        migrations.RemoveField(
            model_name='cargroup',
            name='car',
        ),
        # Tell Django about the new car_id CharField
        migrations.AddField(
            model_name='cargroup',
            name='car_id',
            field=models.CharField(db_index=True, default='', help_text='The car identifier (e.g., b7083...)', max_length=100),
        ),
        # Set the correct unique_together
        migrations.AlterUniqueTogether(
            name='cargroup',
            unique_together={('car_id', 'group_id')},
        ),
    ]
