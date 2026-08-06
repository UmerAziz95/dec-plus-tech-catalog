from django.conf import settings
from django.db import models


class Car(models.Model):
    """
    Stores car/vehicle information imported from the 'CarId' sheet.
    Each car has a unique car_id (e.g., MIT1000001).
    """
    car_id = models.TextField(
        unique=True, db_index=True,
        help_text="Unique car identifier from Excel (e.g., MIT1000001)"
    )
    car_model = models.TextField(
        blank=True,
        help_text="Full car model string (e.g., MITSUBISHI 3000GT Z16A [MJGFL6] 1990-2000)"
    )
    steering = models.TextField(
        blank=True,
        help_text="Steering type (e.g., LHD, RHD)"
    )
    transmission = models.TextField(
        blank=True,
        help_text="Transmission type (e.g., MT, AT)"
    )
    wd = models.TextField(
        blank=True,
        help_text="Wheel drive type (e.g., 2WD, 4WD)"
    )
    engine = models.TextField(
        blank=True,
        help_text="Engine specification"
    )
    car_parameters = models.TextField(
        blank=True,
        help_text="Car parameters (e.g., 3000/4WD/4WS)"
    )
    additional_note = models.TextField(
        blank=True,
        help_text="Additional notes (e.g., DOHC(TURBO/4WD/4WS),6FM/T)"
    )
    metadata_json = models.JSONField(default=dict, blank=True)

    import_batch = models.ForeignKey(
        'ImportBatch',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='imported_cars',
        help_text="Import batch that created this row, if any. Used to undo an import.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cars'
        verbose_name = 'Car'
        verbose_name_plural = 'Cars'

    def __str__(self):
        return self.car_model if self.car_model else self.car_id


class CarGroup(models.Model):
    """
    Many-to-many relationship between Cars and Groups.
    Imported from the 'carId & GroupId' sheet.
    One car can have many groups, and one group can belong to many cars.
    """
    car_id = models.TextField(
        db_index=True,
        default='',
        help_text="The car identifier (e.g., b7083...)"
    )
    group_id = models.TextField(
        db_index=True,
        default='',
        help_text="The group ID from Excel (e.g., MIT1147463)"
    )

    import_batch = models.ForeignKey(
        'ImportBatch',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='imported_car_groups',
        help_text="Import batch that created this row, if any. Used to undo an import.",
    )

    class Meta:
        db_table = 'car_groups'
        unique_together = ('car_id', 'group_id')
        verbose_name = 'Car-Group Link'
        verbose_name_plural = 'Car-Group Links'

    def __str__(self):
        return f"{self.car_id} -> {self.group_id}"


class Part(models.Model):
    """
    Individual parts linked to a group.
    Imported from the 'groupId & part_number' sheet.
    The part_number is the primary search field.
    """
    group_id = models.TextField(
        db_index=True,
        default='',
        help_text="The group ID this part belongs to"
    )
    brand = models.TextField(
        blank=True,
        help_text="Brand name (e.g., Mitsubishi)"
    )
    part_number = models.TextField(
        db_index=True,
        help_text="Part number - primary search field (e.g., MQ900871)"
    )

    import_batch = models.ForeignKey(
        'ImportBatch',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='imported_parts',
        help_text="Import batch that created this row, if any. Used to undo an import.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'parts'
        indexes = [
            models.Index(fields=['group_id', 'part_number'], name='idx_group_part'),
        ]
        verbose_name = 'Part'
        verbose_name_plural = 'Parts'

    def __str__(self):
        return f"{self.part_number} ({self.brand})"


class CarCrossCode(models.Model):
    """
    Cross Code's counterpart to Car. Same shape, separate table — Cross
    Code is a distinct catalog from Cross Car, not a filtered view of it.
    """
    car_id = models.TextField(
        unique=True, db_index=True,
        help_text="Unique cross-code car identifier"
    )
    car_model = models.TextField(blank=True)
    steering = models.TextField(blank=True)
    transmission = models.TextField(blank=True)
    wd = models.TextField(blank=True)
    engine = models.TextField(blank=True)
    car_parameters = models.TextField(blank=True)
    additional_note = models.TextField(blank=True)
    metadata_json = models.JSONField(default=dict, blank=True)

    import_batch = models.ForeignKey(
        'ImportBatch',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='imported_crosscode_cars',
        help_text="Import batch that created this row, if any. Used to undo an import.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'cars_crosscode'
        verbose_name = 'Cross Code Car'
        verbose_name_plural = 'Cross Code Cars'

    def __str__(self):
        return self.car_model if self.car_model else self.car_id


class CarGroupCrossCode(models.Model):
    """Cross Code's counterpart to CarGroup."""
    car_id = models.TextField(db_index=True, default='')
    group_id = models.TextField(db_index=True, default='')

    import_batch = models.ForeignKey(
        'ImportBatch',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='imported_crosscode_car_groups',
        help_text="Import batch that created this row, if any. Used to undo an import.",
    )

    class Meta:
        db_table = 'car_groups_crosscode'
        unique_together = ('car_id', 'group_id')
        verbose_name = 'Cross Code Car-Group Link'
        verbose_name_plural = 'Cross Code Car-Group Links'

    def __str__(self):
        return f"{self.car_id} -> {self.group_id}"


class PartCrossCode(models.Model):
    """
    Cross Code catalog row from the Product Brand / Product No / Brand / Code
    workbook format. ``brand`` is Product Brand, ``product_no`` is Product No,
    ``oe_brand`` is Brand, and ``part_number`` is Code (searchable OE/cross code).
    ``group_id`` is set to Product No so related codes share a group key.
    """
    group_id = models.TextField(db_index=True, default='')
    brand = models.TextField(blank=True, help_text='Product Brand (e.g. NIBK)')
    product_no = models.TextField(blank=True, db_index=True, help_text='Product No (e.g. PN0150W)')
    oe_brand = models.TextField(blank=True, help_text='OE/cross Brand (e.g. MITSUBISHI)')
    part_number = models.TextField(db_index=True, help_text='Code (e.g. 4605A049)')

    import_batch = models.ForeignKey(
        'ImportBatch',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='imported_crosscode_parts',
        help_text="Import batch that created this row, if any. Used to undo an import.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'parts_crosscode'
        indexes = [
            models.Index(fields=['group_id', 'part_number'], name='idx_group_part_crosscode'),
            models.Index(fields=['brand', 'product_no'], name='idx_product_crosscode'),
        ]
        verbose_name = 'Cross Code Part'
        verbose_name_plural = 'Cross Code Parts'

    def __str__(self):
        return f"{self.brand} {self.product_no} → {self.oe_brand} {self.part_number}".strip()


class Basket(models.Model):
    brand = models.TextField(
        help_text="Cross-reference brand name (e.g., Kanoya)",
    )
    brand_number = models.TextField(
        blank=True,
        help_text="Cross-reference brand part number (e.g., C13X20)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'basket'
        ordering = ['-id']
        verbose_name = 'Basket'
        verbose_name_plural = 'Baskets'
        constraints = [
            models.UniqueConstraint(
                fields=['brand', 'brand_number'],
                name='uniq_basket_brand_pair',
            ),
        ]

    def __str__(self):
        return f'{self.brand} | {self.brand_number}'


class BasketItem(models.Model):
    basket = models.ForeignKey(
        Basket,
        on_delete=models.CASCADE,
        related_name='items',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='basket_items',
    )
    car = models.ForeignKey(
        Car,
        on_delete=models.CASCADE,
        related_name='basket_items',
    )
    part = models.ForeignKey(
        Part,
        on_delete=models.CASCADE,
        related_name='basket_items',
    )
    group_id = models.TextField(
        db_index=True,
        blank=True,
        default='',
        help_text="The group ID this basket item belongs to"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'basket_items'
        ordering = ['-created_at']
        verbose_name = 'Basket item'
        verbose_name_plural = 'Basket items'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'car', 'part', 'basket'],
                name='uniq_basket_item_user_car_part_basket',
            ),
        ]

    def __str__(self):
        return f'{self.basket} | {self.part.part_number} | {self.car.car_id}'

    @property
    def brand(self):
        return self.basket.brand

    @property
    def brand_number(self):
        return self.basket.brand_number


class BasketCrossCode(models.Model):
    """Cross Code's counterpart to Basket."""
    brand = models.TextField(
        help_text="Cross-reference brand name (e.g., Kanoya)",
    )
    brand_number = models.TextField(
        blank=True,
        help_text="Cross-reference brand part number (e.g., C13X20)",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'basket_crosscode'
        ordering = ['-id']
        verbose_name = 'Cross Code Basket'
        verbose_name_plural = 'Cross Code Baskets'
        constraints = [
            models.UniqueConstraint(
                fields=['brand', 'brand_number'],
                name='uniq_basket_crosscode_brand_pair',
            ),
        ]

    def __str__(self):
        return f'{self.brand} | {self.brand_number}'


class BasketItemCrossCode(models.Model):
    """
    Cross Code basket line. Grouped under BasketCrossCode (brand name + brand
    number). Points at a PartCrossCode row; car is optional/legacy because the
    Cross Code catalog is flat Product Brand / Product No / Brand / Code.
    """
    basket = models.ForeignKey(
        BasketCrossCode,
        on_delete=models.CASCADE,
        related_name='items',
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='basket_items_crosscode',
    )
    car = models.ForeignKey(
        CarCrossCode,
        on_delete=models.CASCADE,
        related_name='basket_items',
        null=True,
        blank=True,
    )
    part = models.ForeignKey(
        PartCrossCode,
        on_delete=models.CASCADE,
        related_name='basket_items',
    )
    group_id = models.TextField(
        db_index=True,
        blank=True,
        default='',
        help_text="The group ID this basket item belongs to"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'basket_items_crosscode'
        ordering = ['-created_at']
        verbose_name = 'Cross Code Basket item'
        verbose_name_plural = 'Cross Code Basket items'
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'part', 'basket'],
                name='uniq_basket_item_crosscode_user_part_basket',
            ),
        ]

    def __str__(self):
        return f'{self.basket} | {self.part.part_number}'

    @property
    def brand(self):
        return self.basket.brand

    @property
    def brand_number(self):
        return self.basket.brand_number


class ImportBatch(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_PROCESSING = 'processing'
    STATUS_FAILED = 'failed'
    STATUS_COMPLETED = 'completed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_PROCESSING, 'Processing'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_COMPLETED, 'Completed'),
    ]

    TYPE_CARS = 'cars'
    TYPE_GROUPS = 'groups'
    TYPE_PARTS = 'parts'
    TYPE_CAR_WITH_PARTS = 'car_with_parts'
    TYPE_CARS_CROSSCODE = 'cars_crosscode'
    TYPE_GROUPS_CROSSCODE = 'groups_crosscode'
    TYPE_PARTS_CROSSCODE = 'parts_crosscode'
    IMPORT_TYPE_CHOICES = [
        (TYPE_CARS, 'Car models'),
        (TYPE_GROUPS, 'Groups and links'),
        (TYPE_PARTS, 'Part numbers'),
        (TYPE_CAR_WITH_PARTS, 'Car with parts'),
        (TYPE_CARS_CROSSCODE, 'Cross Code car models'),
        (TYPE_GROUPS_CROSSCODE, 'Cross Code groups and links'),
        (TYPE_PARTS_CROSSCODE, 'Cross Code references'),
    ]

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='import_batches'
    )
    original_file_name = models.TextField()
    import_type = models.CharField(max_length=20, choices=IMPORT_TYPE_CHOICES, default=TYPE_CARS)
    stored_file_path = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING)
    progress_note = models.TextField(blank=True)
    failure_reason = models.TextField(blank=True)
    total_rows = models.PositiveIntegerField(default=0)
    cars_count = models.PositiveIntegerField(default=0)
    groups_count = models.PositiveIntegerField(default=0)
    links_count = models.PositiveIntegerField(default=0)
    parts_count = models.PositiveIntegerField(default=0)
    error_count = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'import_batches'
        ordering = ['-id']

    def __str__(self):
        return f"{self.original_file_name} ({self.status})"

    def history_stats(self):
        stats = []
        if self.total_rows:
            stats.append(f'{self.total_rows:,} row{"s" if self.total_rows != 1 else ""} processed')
        if self.import_type == self.TYPE_CARS and self.cars_count:
            stats.append(f'{self.cars_count:,} car{"s" if self.cars_count != 1 else ""} imported')
        elif self.import_type == self.TYPE_GROUPS:
            if self.groups_count:
                stats.append(f'{self.groups_count:,} group{"s" if self.groups_count != 1 else ""}')
            if self.links_count:
                stats.append(f'{self.links_count:,} link{"s" if self.links_count != 1 else ""}')
        elif self.import_type == self.TYPE_PARTS:
            if self.parts_count:
                stats.append(f'{self.parts_count:,} part{"s" if self.parts_count != 1 else ""} imported')
            if self.groups_count:
                stats.append(f'{self.groups_count:,} group{"s" if self.groups_count != 1 else ""} created')
        elif self.import_type == self.TYPE_CAR_WITH_PARTS:
            if self.cars_count:
                stats.append(f'{self.cars_count:,} car{"s" if self.cars_count != 1 else ""} added')
            if self.parts_count:
                stats.append(f'{self.parts_count:,} part{"s" if self.parts_count != 1 else ""} added')
        elif self.import_type == self.TYPE_CARS_CROSSCODE and self.cars_count:
            stats.append(f'{self.cars_count:,} car{"s" if self.cars_count != 1 else ""} imported')
        elif self.import_type == self.TYPE_GROUPS_CROSSCODE:
            if self.groups_count:
                stats.append(f'{self.groups_count:,} group{"s" if self.groups_count != 1 else ""}')
            if self.links_count:
                stats.append(f'{self.links_count:,} link{"s" if self.links_count != 1 else ""}')
        elif self.import_type == self.TYPE_PARTS_CROSSCODE:
            if self.parts_count:
                stats.append(f'{self.parts_count:,} part{"s" if self.parts_count != 1 else ""} imported')
            if self.groups_count:
                stats.append(f'{self.groups_count:,} group{"s" if self.groups_count != 1 else ""} created')
        if self.error_count:
            label = 'warnings' if self.status == self.STATUS_COMPLETED else 'issues'
            stats.append(f'{self.error_count:,} {label}')
        if self.status in (self.STATUS_PENDING, self.STATUS_PROCESSING) and self.progress_note:
            stats.append(self.progress_note)
        return stats


class ImportRowError(models.Model):
    batch = models.ForeignKey(
        ImportBatch,
        on_delete=models.CASCADE,
        related_name='row_errors'
    )
    sheet_name = models.TextField()
    row_number = models.PositiveIntegerField()
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'import_row_errors'
        ordering = ['-id']

    def __str__(self):
        return f"{self.sheet_name} row {self.row_number}"
