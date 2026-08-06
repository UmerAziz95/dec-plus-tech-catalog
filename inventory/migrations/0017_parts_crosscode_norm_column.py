"""
Mirrors migration 0013's performance setup, but for the new parts_crosscode
table: a stored generated column for normalized part numbers, plus a GIN
trigram index (substring search) and B-tree index (exact match). The table
starts empty, so CONCURRENTLY isn't needed here the way it was for the
original 135M-row `parts` table.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0016_alter_importbatch_import_type_carcrosscode_and_more'),
    ]

    operations = [
        migrations.RunSQL(
            sql="""
                ALTER TABLE parts_crosscode
                ADD COLUMN IF NOT EXISTS part_number_norm text
                GENERATED ALWAYS AS (
                    upper(regexp_replace(part_number, '[^A-Za-z0-9]', '', 'g'))
                ) STORED;
            """,
            reverse_sql="""
                ALTER TABLE parts_crosscode DROP COLUMN IF EXISTS part_number_norm;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX IF NOT EXISTS idx_parts_crosscode_norm_trgm
                ON parts_crosscode USING gin (part_number_norm gin_trgm_ops);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS idx_parts_crosscode_norm_trgm;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX IF NOT EXISTS idx_parts_crosscode_norm_btree
                ON parts_crosscode (part_number_norm);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS idx_parts_crosscode_norm_btree;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX IF NOT EXISTS idx_cargroups_crosscode_groupid_carid
                ON car_groups_crosscode (group_id, car_id);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS idx_cargroups_crosscode_groupid_carid;
            """,
        ),
        migrations.RunSQL(
            sql="""
                CREATE INDEX IF NOT EXISTS idx_cargroups_crosscode_carid_groupid
                ON car_groups_crosscode (car_id, group_id);
            """,
            reverse_sql="""
                DROP INDEX IF EXISTS idx_cargroups_crosscode_carid_groupid;
            """,
        ),
    ]
