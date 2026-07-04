from django.urls import path
from . import views

app_name = 'inventory'

urlpatterns = [
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('import/', views.import_data_view, name='import_data'),
    path('import/status/<int:batch_id>/', views.import_batch_status_view, name='import_batch_status'),
    path('import/history/', views.import_history_view, name='import_history'),
    path('import/<int:batch_id>/delete/', views.import_batch_delete_view, name='import_batch_delete'),
    path('search/', views.search_part_view, name='search_part'),
    path('search/add-all-to-basket/', views.add_search_results_to_basket, name='add_search_results_to_basket'),
    path('search/bulk-missed-export/', views.bulk_search_missed_export_view, name='bulk_search_missed_export'),
    path('search/bulk-sample-export/', views.bulk_search_sample_export_view, name='bulk_search_sample_export'),
    path('search/export/', views.part_search_export_view, name='part_search_export'),
    path('search/bulk-results-export/', views.bulk_search_results_export_view, name='bulk_search_results_export'),
    path('cars/', views.cars_catalog_view, name='cars_catalog'),
    path('cars/suggestions/', views.cars_catalog_suggestions_view, name='cars_catalog_suggestions'),
    path('cars/export/', views.cars_catalog_export_view, name='cars_catalog_export'),
    path('cars/<int:car_pk>/update/', views.cars_catalog_update_view, name='cars_catalog_update'),
    path('basket/', views.basket_view, name='basket'),
    path('basket/group/<int:basket_id>/', views.basket_group_detail_view, name='basket_group_detail'),
    path('basket/export/', views.basket_export_view, name='basket_export'),
    path('basket/remove-duplicates/', views.remove_basket_duplicates_view, name='remove_basket_duplicates'),
    path('basket/clear/', views.clear_basket_view, name='clear_basket'),
    path('basket/add/', views.add_to_basket, name='add_to_basket'),
    path('basket/<int:basket_id>/remove/', views.remove_from_basket, name='remove_from_basket'),
    path('basket/<int:basket_id>/update/', views.update_basket_item, name='update_basket_item'),
    path('book-search/', views.book_search_view, name='book_search'),
    # External DB sync
    path('sync-external/', views.sync_external_db_view, name='sync_external'),
    path('sync-external/trigger/', views.trigger_sync_view, name='sync_trigger'),
    path('sync-external/status/', views.sync_status_view, name='sync_status'),
]
