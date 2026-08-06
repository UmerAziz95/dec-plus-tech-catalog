from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0018_basketcrosscode_basketitemcrosscode'),
    ]

    operations = [
        migrations.AddField(
            model_name='partcrosscode',
            name='product_no',
            field=models.TextField(blank=True, db_index=True, default='', help_text='Product No (e.g. PN0150W)'),
        ),
        migrations.AddField(
            model_name='partcrosscode',
            name='oe_brand',
            field=models.TextField(blank=True, default='', help_text='OE/cross Brand (e.g. MITSUBISHI)'),
        ),
        migrations.AlterField(
            model_name='partcrosscode',
            name='brand',
            field=models.TextField(blank=True, help_text='Product Brand (e.g. NIBK)'),
        ),
        migrations.AlterField(
            model_name='partcrosscode',
            name='part_number',
            field=models.TextField(db_index=True, help_text='Code (e.g. 4605A049)'),
        ),
        migrations.AlterField(
            model_name='importbatch',
            name='import_type',
            field=models.CharField(
                choices=[
                    ('cars', 'Car models'),
                    ('groups', 'Groups and links'),
                    ('parts', 'Part numbers'),
                    ('car_with_parts', 'Car with parts'),
                    ('cars_crosscode', 'Cross Code car models'),
                    ('groups_crosscode', 'Cross Code groups and links'),
                    ('parts_crosscode', 'Cross Code references'),
                ],
                default='cars',
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name='partcrosscode',
            index=models.Index(fields=['brand', 'product_no'], name='idx_product_crosscode'),
        ),
    ]
