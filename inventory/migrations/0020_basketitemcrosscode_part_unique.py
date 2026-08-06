from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0019_partcrosscode_product_no_oe_brand'),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name='basketitemcrosscode',
            name='uniq_basket_item_crosscode_user_car_part_basket',
        ),
        migrations.AlterField(
            model_name='basketitemcrosscode',
            name='car',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='basket_items',
                to='inventory.carcrosscode',
            ),
        ),
        migrations.AddConstraint(
            model_name='basketitemcrosscode',
            constraint=models.UniqueConstraint(
                fields=('user', 'part', 'basket'),
                name='uniq_basket_item_crosscode_user_part_basket',
            ),
        ),
    ]
