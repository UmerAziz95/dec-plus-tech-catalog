"""
Performance optimization migration:
1. Enable pg_trgm extension for trigram indexing
2. Add a stored generated column `part_number_norm` on `parts` table
3. Create GIN trigram index on `part_number_norm` for blazing-fast substring search
4. Create composite covering indexes on `car_groups`
5. Remove the duplicate idx_part_number index
6. Remove default ordering from Car and Part models
"""

from django.db import migrations


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ('inventory', '0012_change_charfield_to_textfield'),
    ]

    operations = [
        # ── 1. Enable pg_trgm extension ──────────────────────────────────
        migrations.RunSQL(
            sql="CREATE EXTENSION IF NOT EXISTS pg_trgm;",
            reverse_sql="DROP EXTENSION IF EXISTS pg_trgm;",
        ),

        # ── 2. Add stored generated column for normalized part numbers ───
        # This pre-computes regexp_replace at INSERT/UPDATE time so we
        # never have to run it at query time across 135M rows.
        migrations.RunSQL(
            sql="""
                ALTER TABLE parts
                ADD COLUMN IF NOT EXISTS part_number_norm text
                GENERATED ALWAYS AS (
                    upper(regexp_replace(part_number, '[^A-Za-z0-9]', '', 'g'))
                ) STORED;
            """,
            reverse_sql="""
                ALTER TABLE parts DROP COLUMN IF EXISTS part_number_norm;
            """,
        ),

        # ── 3. GIN trigram index on the normalized column ────────────────
        # This makes LIKE '%xyz%' and ILIKE queries use the index instead
        # of scanning all 135M rows.
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_parts_norm_trgm
                ON parts USING gin (part_number_norm gin_trgm_ops);
            """,
            reverse_sql="""
                DROP INDEX CONCURRENTLY IF EXISTS idx_parts_norm_trgm;
            """,
        ),

        # ── 4. B-tree index on normalized column for exact match ─────────
        # Exact match (=) is faster on B-tree than GIN trigram.
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_parts_norm_btree
                ON parts (part_number_norm);
            """,
            reverse_sql="""
                DROP INDEX CONCURRENTLY IF EXISTS idx_parts_norm_btree;
            """,
        ),

        # ── 5. Composite covering index on car_groups ────────────────────
        # When we look up car_groups by group_id, we always need car_id.
        # This composite index lets PG answer the query from the index
        # alone without hitting the main table (index-only scan).
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cargroups_groupid_carid
                ON car_groups (group_id, car_id);
            """,
            reverse_sql="""
                DROP INDEX CONCURRENTLY IF EXISTS idx_cargroups_groupid_carid;
            """,
        ),

        # ── 6. Reverse composite index on car_groups ─────────────────────
        # For lookups by car_id → group_id (used in car catalog features).
        migrations.RunSQL(
            sql="""
                CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_cargroups_carid_groupid
                ON car_groups (car_id, group_id);
            """,
            reverse_sql="""
                DROP INDEX CONCURRENTLY IF EXISTS idx_cargroups_carid_groupid;
            """,
        ),

        # ── 7. Drop duplicate idx_part_number ────────────────────────────
        # db_index=True already creates parts_part_number_xxx; this one is redundant.
        migrations.RunSQL(
            sql="DROP INDEX IF EXISTS idx_part_number;",
            reverse_sql="CREATE INDEX idx_part_number ON parts (part_number);",
        ),

        # ── 8. Model ordering changes ────────────────────────────────────
        migrations.AlterModelOptions(
            name='car',
            options={'verbose_name': 'Car', 'verbose_name_plural': 'Cars'},
        ),
        migrations.AlterModelOptions(
            name='part',
            options={'verbose_name': 'Part', 'verbose_name_plural': 'Parts'},
        ),

        # ── 9. Remove duplicate index from model Meta ────────────────────
        migrations.RemoveIndex(
            model_name='part',
            name='idx_part_number',
        ),
    ]
