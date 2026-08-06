from django.contrib import admin
from .models import (
    Basket,
    BasketCrossCode,
    BasketItem,
    BasketItemCrossCode,
    Car,
    CarCrossCode,
    CarGroup,
    CarGroupCrossCode,
    ImportBatch,
    ImportRowError,
    Part,
    PartCrossCode,
)


@admin.register(Car)
class CarAdmin(admin.ModelAdmin):
    """Admin configuration for Car model."""
    list_display = ('car_id', 'car_model', 'steering', 'transmission', 'wd', 'engine', 'created_at')
    list_filter = ('steering', 'transmission', 'wd')
    search_fields = ('car_id', 'car_model', 'engine', 'car_parameters')
    list_per_page = 25


@admin.register(CarGroup)
class CarGroupAdmin(admin.ModelAdmin):
    """Admin configuration for Car-Group link model."""
    list_display = ('id', 'car_id', 'group_id')
    search_fields = ('car_id', 'group_id')
    list_per_page = 25


@admin.register(Part)
class PartAdmin(admin.ModelAdmin):
    """Admin configuration for Part model."""
    list_display = ('part_number', 'brand', 'group_id', 'created_at')
    list_filter = ('brand',)
    search_fields = ('part_number', 'group_id')
    list_per_page = 25


@admin.register(CarCrossCode)
class CarCrossCodeAdmin(admin.ModelAdmin):
    list_display = ('car_id', 'car_model', 'steering', 'transmission', 'wd', 'engine', 'created_at')
    list_filter = ('steering', 'transmission', 'wd')
    search_fields = ('car_id', 'car_model', 'engine', 'car_parameters')
    list_per_page = 25


@admin.register(CarGroupCrossCode)
class CarGroupCrossCodeAdmin(admin.ModelAdmin):
    list_display = ('id', 'car_id', 'group_id')
    search_fields = ('car_id', 'group_id')
    list_per_page = 25


@admin.register(PartCrossCode)
class PartCrossCodeAdmin(admin.ModelAdmin):
    list_display = ('brand', 'product_no', 'oe_brand', 'part_number', 'group_id', 'created_at')
    list_filter = ('brand', 'oe_brand')
    search_fields = ('brand', 'product_no', 'oe_brand', 'part_number', 'group_id')
    list_per_page = 25


@admin.register(Basket)
class BasketAdmin(admin.ModelAdmin):
    list_display = ('id', 'brand', 'brand_number', 'created_at')
    list_filter = ('brand',)
    search_fields = ('brand', 'brand_number')
    list_per_page = 25


@admin.register(BasketItem)
class BasketItemAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'basket', 'part', 'car', 'group_id', 'created_at')
    list_filter = ('basket__brand',)
    search_fields = ('basket__brand', 'basket__brand_number', 'part__part_number', 'car__car_id', 'group_id')
    raw_id_fields = ('user', 'basket', 'car', 'part')
    list_per_page = 25


@admin.register(BasketCrossCode)
class BasketCrossCodeAdmin(admin.ModelAdmin):
    list_display = ('id', 'brand', 'brand_number', 'created_at')
    list_filter = ('brand',)
    search_fields = ('brand', 'brand_number')
    list_per_page = 25


@admin.register(BasketItemCrossCode)
class BasketItemCrossCodeAdmin(admin.ModelAdmin):
    list_display = ('id', 'user', 'basket', 'part', 'car', 'group_id', 'created_at')
    list_filter = ('basket__brand',)
    search_fields = ('basket__brand', 'basket__brand_number', 'part__part_number', 'car__car_id', 'group_id')
    raw_id_fields = ('user', 'basket', 'car', 'part')
    list_per_page = 25


@admin.register(ImportBatch)
class ImportBatchAdmin(admin.ModelAdmin):
    list_display = ('id', 'import_type', 'original_file_name', 'status', 'progress_note', 'cars_count', 'groups_count', 'links_count', 'parts_count', 'error_count', 'created_at')
    list_filter = ('status', 'import_type')
    search_fields = ('original_file_name',)
    raw_id_fields = ('uploaded_by',)
    list_per_page = 25


@admin.register(ImportRowError)
class ImportRowErrorAdmin(admin.ModelAdmin):
    list_display = ('id', 'batch', 'sheet_name', 'row_number', 'created_at')
    search_fields = ('sheet_name', 'message')
    raw_id_fields = ('batch',)
    list_per_page = 25
