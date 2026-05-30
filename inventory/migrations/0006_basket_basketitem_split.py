from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def forwards_copy_basket_rows(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT id, brand, brand_number, user_id, car_id, part_id, created_at, updated_at
            FROM basket_old
            ORDER BY id
            """
        )
        rows = cursor.fetchall()

    Basket = apps.get_model('inventory', 'Basket')
    BasketItem = apps.get_model('inventory', 'BasketItem')
    basket_cache = {}

    for _old_id, brand, brand_number, user_id, car_id, part_id, created_at, updated_at in rows:
        key = (brand or '', brand_number or '')
        basket_pk = basket_cache.get(key)
        if basket_pk is None:
            basket, _created = Basket.objects.get_or_create(
                brand=brand or '',
                brand_number=brand_number or '',
            )
            basket_pk = basket.pk
            basket_cache[key] = basket_pk

        BasketItem.objects.create(
            basket_id=basket_pk,
            user_id=user_id,
            car_id=car_id,
            part_id=part_id,
            created_at=created_at,
            updated_at=updated_at,
        )


def backwards_copy_basket_rows(apps, schema_editor):
    BasketItem = apps.get_model('inventory', 'BasketItem')
    with schema_editor.connection.cursor() as cursor:
        for item in BasketItem.objects.select_related('basket').iterator():
            cursor.execute(
                """
                INSERT INTO basket_old (
                    brand, brand_number, user_id, car_id, part_id, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    item.basket.brand,
                    item.basket.brand_number,
                    item.user_id,
                    item.car_id,
                    item.part_id,
                    item.created_at,
                    item.updated_at,
                ],
            )


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_remove_basket_notes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql='ALTER TABLE basket RENAME TO basket_old;',
                    reverse_sql='ALTER TABLE basket_old RENAME TO basket;',
                ),
            ],
            state_operations=[
                migrations.DeleteModel(name='Basket'),
            ],
        ),
        migrations.CreateModel(
            name='Basket',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('brand', models.CharField(help_text='Cross-reference brand name (e.g., Kanoya)', max_length=255)),
                ('brand_number', models.CharField(blank=True, help_text='Cross-reference brand part number (e.g., C13X20)', max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Basket',
                'verbose_name_plural': 'Baskets',
                'db_table': 'basket',
                'ordering': ['-id'],
            },
        ),
        migrations.CreateModel(
            name='BasketItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('basket', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='inventory.basket')),
                ('car', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='basket_items', to='inventory.car')),
                ('part', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='basket_items', to='inventory.part')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='basket_items', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Basket item',
                'verbose_name_plural': 'Basket items',
                'db_table': 'basket_items',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='basket',
            constraint=models.UniqueConstraint(fields=('brand', 'brand_number'), name='uniq_basket_brand_pair'),
        ),
        migrations.AddConstraint(
            model_name='basketitem',
            constraint=models.UniqueConstraint(
                fields=('user', 'car', 'part', 'basket'),
                name='uniq_basket_item_user_car_part_basket',
            ),
        ),
        migrations.RunPython(forwards_copy_basket_rows, backwards_copy_basket_rows),
        migrations.RunSQL(
            sql='DROP TABLE basket_old;',
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
