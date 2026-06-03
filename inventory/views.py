import json
import threading

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.management import call_command
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from urllib.parse import urlencode

from .forms import ExcelImportForm
from .models import Car, CarGroup, Part, Basket, BasketItem, ImportBatch
from .services.basket_service import BasketService
from .services.bulk_search_service import BulkSearchService
from .services.car_catalog_service import CarCatalogService
from .services.import_services import ExcelImportService
from .services.part_number_utils import sanitize_part_number
from .services.part_search_service import PartSearchService

# ── In-memory state for the one-time external DB sync ──
_sync_lock = threading.Lock()
_sync_state = {
    'status': 'idle',       # idle | running | completed | failed
    'parts_synced': 0,
    'car_groups_synced': 0,
    'error': '',
}



def _basket_redirect_params(request):
    query = BasketService.clean_search_query(request.POST.get('q', ''))
    page = BasketService.parse_page_number(request.POST.get('page', 1))
    return query, page


def _redirect_to_basket(request, user):
    query, page = _basket_redirect_params(request)
    total = BasketService.filter_items_queryset(user, query).count()
    page = BasketService.page_after_delete(page, total)
    params = BasketService.list_query_params(query, page)
    url = reverse('inventory:basket')
    if params:
        url = f'{url}?{urlencode(params)}'
    return redirect(url)


def _redirect_to_basket_group(request, user, basket_id):
    query, page = _basket_redirect_params(request)
    total = BasketService.filter_group_items_queryset(user, basket_id, query).count()
    page = BasketService.page_after_delete(page, total)
    params = BasketService.list_query_params(query, page)
    url = reverse('inventory:basket_group_detail', kwargs={'basket_id': basket_id})
    if params:
        url = f'{url}?{urlencode(params)}'
    return redirect(url)


@login_required
def dashboard_view(request):
    """Main dashboard with database statistics."""
    context = {
        'active_page': 'dashboard',
        'total_cars': Car.objects.count(),
        'total_parts': Part.objects.count(),
        'basket_count': BasketService.count_for_user(request.user),
    }
    return render(request, 'inventory/dashboard.html', context)


@login_required
def import_data_view(request):
    active_batch = None
    form = ExcelImportForm(initial={'import_type': ImportBatch.TYPE_CARS})
    if request.method == 'POST':
        form = ExcelImportForm(request.POST, request.FILES)
        if form.is_valid():
            batch_id = ExcelImportService.enqueue(
                form.cleaned_data['excel_file'],
                request.user,
                form.cleaned_data['import_type'],
            )
            messages.success(
                request,
                'Import started in the background. This page will show progress; you can leave and come back.',
            )
            return redirect(f"{request.path}?batch={batch_id}")
        messages.error(request, 'Please fix validation errors and try again.')
    batch_id = request.GET.get('batch')
    if batch_id and str(batch_id).isdigit():
        active_batch = ImportBatch.objects.filter(
            pk=int(batch_id),
            uploaded_by=request.user,
        ).first()
    history_limit = 10
    user_batches = ImportBatch.objects.filter(uploaded_by=request.user)
    context = {
        'active_page': 'import_data',
        'form': form,
        'active_batch': active_batch,
        'import_types': ImportBatch.IMPORT_TYPE_CHOICES,
        'cars_import_history': user_batches.filter(import_type=ImportBatch.TYPE_CARS).order_by('-created_at')[:history_limit],
        'groups_import_history': user_batches.filter(import_type=ImportBatch.TYPE_GROUPS).order_by('-created_at')[:history_limit],
        'parts_import_history': user_batches.filter(import_type=ImportBatch.TYPE_PARTS).order_by('-created_at')[:history_limit],
        'basket_count': BasketService.count_for_user(request.user),
        'max_upload_size_bytes': getattr(settings, 'IMPORT_MAX_UPLOAD_SIZE_BYTES', 5 * 1024 * 1024 * 1024),
        'max_upload_size_gb': getattr(settings, 'IMPORT_MAX_UPLOAD_SIZE_GB', 5),
        'import_groups_column_pairs': getattr(settings, 'IMPORT_GROUPS_COLUMN_PAIRS', 3),
        'import_parts_column_blocks': getattr(settings, 'IMPORT_PARTS_COLUMN_BLOCKS', 6),
        'import_history_url': reverse('inventory:import_history'),
    }
    return render(request, 'inventory/import_data.html', context)


@login_required
def import_history_view(request):
    history_limit = 10
    user_batches = ImportBatch.objects.filter(uploaded_by=request.user)
    html = {}
    poll_active = {}
    for import_type, _label in ImportBatch.IMPORT_TYPE_CHOICES:
        history = list(
            user_batches.filter(import_type=import_type).order_by('-created_at')[:history_limit]
        )
        html[import_type] = render_to_string(
            'inventory/partials/import_history_items.html',
            {'history': history},
            request=request,
        )
        poll_active[import_type] = bool(
            history
            and history[0].status in (ImportBatch.STATUS_PENDING, ImportBatch.STATUS_PROCESSING)
        )
    return JsonResponse({
        'html': html,
        'poll_active': poll_active,
        'poll': any(poll_active.values()),
    })


@login_required
def import_batch_status_view(request, batch_id):
    batch = get_object_or_404(ImportBatch, pk=batch_id, uploaded_by=request.user)
    errors = []
    if batch.status == ImportBatch.STATUS_FAILED or batch.error_count:
        errors = list(
            batch.row_errors.order_by('-id').values('sheet_name', 'row_number', 'message')[:500]
        )
    return JsonResponse({
        'status': True,
        'data': {
            'batch_id': batch.id,
            'import_type': batch.import_type,
            'original_file_name': batch.original_file_name,
            'job_status': batch.status,
            'progress_note': batch.progress_note,
            'failure_reason': batch.failure_reason,
            'total_rows': batch.total_rows,
            'cars_count': batch.cars_count,
            'groups_count': batch.groups_count,
            'links_count': batch.links_count,
            'parts_count': batch.parts_count,
            'error_count': batch.error_count,
            'completed_at': batch.completed_at.isoformat() if batch.completed_at else None,
        },
        'errors': errors,
    })


def _complete_bulk_search(request, rows_data):
    bulk_results, bulk_summary, error_message = BulkSearchService.run_bulk_search(
        request.user,
        rows_data,
        save_to_basket=True,
    )
    if error_message:
        messages.error(request, error_message)
        return redirect('inventory:search_part')
    request.session['bulk_search_missed_rows'] = bulk_summary.get('missed_rows', [])
    request.session['bulk_search_export_rows'] = bulk_results
    request.session['bulk_search_summary'] = bulk_summary
    messages.success(
        request,
        f'Bulk search complete: {bulk_summary.get("added_to_basket", 0)} item(s) added to basket.',
    )
    return redirect(f"{reverse('inventory:search_part')}?bulk_done=1#search-results-panel")


@login_required
def search_part_view(request):
    """
    Part number search and bulk Excel search on one page.
  """
    raw_query = request.GET.get('q', '').strip()
    search_query = sanitize_part_number(raw_query)
    results = []
    total_cars = 0
    total_parts_found = 0
    bulk_results = []
    bulk_has_searched = False
    bulk_summary = None

    if request.GET.get('bulk_done') and request.session.get('bulk_search_export_rows') is not None:
        bulk_results = request.session.get('bulk_search_export_rows') or []
        bulk_summary = request.session.pop('bulk_search_summary', None)
        bulk_has_searched = True

    if request.method == 'POST' and request.FILES.get('excel_file'):
        rows_data, error_message = BulkSearchService.parse_upload(request.FILES['excel_file'])
        if error_message:
            messages.error(request, error_message)
        elif not rows_data:
            messages.warning(request, 'The uploaded file did not contain any valid rows.')
        else:
            return _complete_bulk_search(request, rows_data)

    if search_query:
        results = PartSearchService.build_results(raw_query)
        total_cars = len(results)
        total_parts_found = len({
            part.id
            for item in results
            for part in item['parts']
        })

    # Paginate results
    page_number = request.GET.get('page', 1)
    paginator = Paginator(results, 25)  # 25 cars per page
    page_obj = paginator.get_page(page_number)

    bulk_missed_rows = request.session.get('bulk_search_missed_rows') or []
    bulk_missed_count = len(bulk_missed_rows)

    context = {
        'active_page': 'search_part',
        'query': raw_query,
        'search_query': search_query,
        'page_obj': page_obj,
        'total_cars': total_cars,
        'total_parts_found': total_parts_found,
        'has_results': bool(search_query and results),
        'searched': bool(search_query),
        'bulk_results': bulk_results,
        'bulk_has_searched': bulk_has_searched,
        'bulk_result_count': len(bulk_results),
        'bulk_summary': bulk_summary,
        'bulk_missed_count': bulk_missed_count,
        'part_search_export_url': reverse('inventory:part_search_export'),
        'basket_count': BasketService.count_for_user(request.user),
    }
    return render(request, 'inventory/search_part.html', context)


@login_required
def bulk_search_missed_export_view(request):
    missed_rows = request.session.get('bulk_search_missed_rows') or []
    if not missed_rows:
        messages.error(request, 'No missed bulk search rows are available to export.')
        return redirect('inventory:search_part')

    workbook = BulkSearchService.build_missed_workbook(missed_rows)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="bulk_search_missed.xlsx"'
    workbook.save(response)
    return response


@login_required
def part_search_export_view(request):
    raw_query = request.GET.get('q', '').strip()
    search_query = sanitize_part_number(raw_query)
    if not search_query:
        messages.error(request, 'Run a part number search before exporting.')
        return redirect('inventory:search_part')

    results = PartSearchService.build_results(raw_query)
    if not results:
        messages.error(request, 'No vehicles to export for this search.')
        return redirect(f"{reverse('inventory:search_part')}?{urlencode({'q': raw_query})}")

    workbook = PartSearchService.build_export_workbook(raw_query, results)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="part_search_results.xlsx"'
    workbook.save(response)
    return response


@login_required
def bulk_search_results_export_view(request):
    if 'bulk_search_export_rows' not in request.session:
        messages.error(request, 'No bulk search results to export. Upload a file and run bulk search first.')
        return redirect('inventory:search_part')

    results = request.session.get('bulk_search_export_rows') or []
    workbook = BulkSearchService.build_results_workbook(results)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="bulk_search_results.xlsx"'
    workbook.save(response)
    return response


@login_required
def book_search_view(request):
    return redirect('inventory:search_part')


# ============================================
# BASKET VIEWS
# ============================================

@login_required
@require_POST
def add_search_results_to_basket(request):
    raw_query = request.POST.get('q', '').strip()
    search_query = sanitize_part_number(raw_query)
    brand = request.POST.get('brand', '').strip()
    brand_number = request.POST.get('brand_number', '').strip()
    page = request.POST.get('page', '').strip()

    if not search_query or not brand or not brand_number:
        messages.error(request, 'Search query, brand name, and brand number are required.')
        return redirect('inventory:search_part')

    results = PartSearchService.build_results(raw_query)
    if not results:
        messages.error(request, 'No search results are available to add to the basket.')
        return redirect(f"{reverse('inventory:search_part')}?{urlencode({'q': raw_query})}")

    added_count = PartSearchService.add_results_to_basket(
        request.user,
        results,
        brand,
        brand_number,
    )
    if added_count:
        messages.success(
            request,
            f'Added {added_count} item(s) to your basket from the full search for "{search_query}".',
        )
    else:
        messages.warning(request, 'All matching search results are already in your basket.')

    params = {'q': raw_query}
    if page.isdigit():
        params['page'] = page
    return redirect(f"{reverse('inventory:search_part')}?{urlencode(params)}")


@login_required
@require_POST
def add_to_basket(request):
    """
    Add a car+part to basket with brand assignment.
    Expects POST data: car_id (pk), part_id (pk), brand, brand_number.
    Redirects back to search results with a success message.
    """
    car_id = request.POST.get('car_id')
    part_id = request.POST.get('part_id')
    brand = request.POST.get('brand', '').strip()
    brand_number = request.POST.get('brand_number', '').strip()

    if not car_id or not part_id or not brand or not brand_number:
        messages.error(request, 'Car, part, brand name, and brand number are required.')
        return redirect(request.META.get('HTTP_REFERER', 'inventory:search_part'))

    car = get_object_or_404(Car, pk=car_id)
    part = get_object_or_404(Part, pk=part_id)

    if BasketService.item_exists(request.user, car, part, brand, brand_number):
        messages.warning(
            request,
            f'This item is already in your basket: {brand} | {part.part_number} | {car.car_id}'
        )
    else:
        BasketService.add_item(request.user, car, part, brand, brand_number)
        messages.success(
            request,
            f'Added to basket: {brand} → {part.part_number} → {car.car_model[:60]}'
        )

    # Redirect back to search results
    referer = request.META.get('HTTP_REFERER', '')
    if referer:
        return redirect(referer)
    return redirect('inventory:search_part')


@login_required
@require_POST
def remove_from_basket(request, basket_id):
    """Remove an item from the user's basket."""
    item = get_object_or_404(BasketItem, pk=basket_id, user=request.user)
    group_basket_id = item.basket_id
    label = f'{item.basket.brand} | {item.part.part_number}'
    BasketService.remove_item(item)
    messages.success(request, f'Removed from basket: {label}')

    return_group = request.POST.get('return_group', '').strip()
    if return_group.isdigit() and int(return_group) == group_basket_id:
        if BasketService.user_owns_basket_group(request.user, int(return_group)):
            return _redirect_to_basket_group(request, request.user, int(return_group))
    return _redirect_to_basket(request, request.user)


@login_required
@require_POST
def update_basket_item(request, basket_id):
    """Update brand and brand number on a basket item."""
    item = get_object_or_404(BasketItem, pk=basket_id, user=request.user)

    brand = request.POST.get('brand', '').strip()
    brand_number = request.POST.get('brand_number', '').strip()

    if not brand or not brand_number:
        messages.error(request, 'Brand name and brand number are required.')
        return _redirect_to_basket(request, request.user)

    basket, _created = BasketService.get_or_create_basket(brand, brand_number)
    item.basket = basket
    item.save(update_fields=['basket', 'updated_at'])
    BasketService.prune_empty_baskets()

    messages.success(request, f'Updated: {brand} | {item.part.part_number}')
    return _redirect_to_basket(request, request.user)


@login_required
def cars_catalog_view(request):
    filters = CarCatalogService.parse_filters(request.GET)
    searched = CarCatalogService.has_search_criteria(filters)
    brands = CarCatalogService.get_brands()
    page_obj = None
    total_cars = 0
    has_results = False

    if searched:
        queryset = CarCatalogService.build_queryset(filters)
        total_cars = queryset.count()
        paginator = Paginator(queryset, 25)
        page_obj = paginator.get_page(request.GET.get('page', 1))
        has_results = bool(page_obj.paginator.count)

    search_params = {key: value for key, value in filters.items() if value}
    search_query_string = urlencode(search_params)

    context = {
        'active_page': 'cars_catalog',
        'brands': brands,
        'filters': filters,
        'searched': searched,
        'page_obj': page_obj,
        'total_cars': total_cars,
        'has_results': has_results,
        'search_query_string': search_query_string,
        'basket_count': BasketService.count_for_user(request.user),
    }
    return render(request, 'inventory/cars_catalog.html', context)


@login_required
def cars_catalog_suggestions_view(request):
    filters = CarCatalogService.parse_filters(request.GET)
    query = CarCatalogService.clean_text(request.GET.get('q', ''), 255)
    suggestions = CarCatalogService.get_suggestions(filters, query)
    return JsonResponse({
        'status': True,
        'data': {'suggestions': suggestions},
    })


@login_required
def cars_catalog_export_view(request):
    filters = CarCatalogService.parse_filters(request.GET)
    if not CarCatalogService.has_search_criteria(filters):
        messages.error(request, 'Select a brand or filters before exporting.')
        return redirect('inventory:cars_catalog')

    queryset = CarCatalogService.build_queryset(filters)
    if not queryset.exists():
        messages.error(request, 'No matching cars to export.')
        return redirect(f"{reverse('inventory:cars_catalog')}?{urlencode({key: value for key, value in filters.items() if value})}")

    workbook = CarCatalogService.build_export_workbook(queryset)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="cars_catalog.xlsx"'
    workbook.save(response)
    return response


@login_required
@require_POST
def cars_catalog_update_view(request, car_pk):
    car = get_object_or_404(Car, pk=car_pk)
    ok, error = CarCatalogService.update_car(car, request.POST)
    next_url = request.POST.get('next', '').strip()
    if next_url.startswith('?'):
        next_url = f"{reverse('inventory:cars_catalog')}{next_url}"
    else:
        next_url = reverse('inventory:cars_catalog')
    if ok:
        messages.success(request, f'Updated car {car.car_id}.')
    else:
        messages.error(request, error)
    return redirect(next_url)


@login_required
def basket_export_view(request):
    if not BasketItem.objects.filter(user=request.user).exists():
        messages.error(request, 'Your basket is empty. Nothing to export.')
        return redirect('inventory:basket')

    workbook = BasketService.build_export_workbook(request.user)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="basket.xlsx"'
    workbook.save(response)
    return response


@login_required
@require_POST
def remove_basket_duplicates_view(request):
    removed_count = BasketService.remove_duplicates(request.user)
    if removed_count:
        messages.success(request, f'Removed {removed_count} duplicate basket item(s).')
    else:
        messages.info(request, 'No duplicate basket rows were found.')
    return redirect('inventory:basket')


@login_required
@require_POST
def clear_basket_view(request):
    removed_count = BasketService.clear_basket(request.user)
    if removed_count:
        messages.success(request, f'Cleared {removed_count} item(s) from your basket.')
    else:
        messages.info(request, 'Your basket is already empty.')
    return redirect('inventory:basket')


@login_required
def basket_view(request):
    """Basket page — shows all user's saved brand assignments."""
    query = BasketService.clean_search_query(request.GET.get('q', ''))
    total_basket_count = BasketService.count_for_user(request.user)

    basket_items = BasketService.filter_items_queryset(
        request.user,
        query,
    ).select_related('part')

    page_number = request.GET.get('page', 1)
    paginator = Paginator(basket_items, BasketService.BASKET_PAGE_SIZE)
    page_obj = paginator.get_page(page_number)

    search_params = {'q': query} if query else {}
    search_query_string = urlencode(search_params)

    context = {
        'active_page': 'basket',
        'page_obj': page_obj,
        'query': query,
        'filtered_count': paginator.count,
        'total_basket_count': total_basket_count,
        'basket_count': total_basket_count,
        'has_results': paginator.count > 0,
        'searched': bool(query),
        'search_query_string': search_query_string,
    }
    return render(request, 'inventory/basket.html', context)


@login_required
def basket_group_detail_view(request, basket_id):
    if not BasketService.user_owns_basket_group(request.user, basket_id):
        messages.error(request, 'This brand group is not in your basket.')
        return redirect('inventory:basket')

    active_basket = get_object_or_404(Basket, pk=basket_id)
    basket_groups = BasketService.get_user_basket_groups(request.user)

    query = BasketService.clean_search_query(request.GET.get('q', ''))
    group_total_count = BasketService.get_group_items_queryset(request.user, basket_id).count()
    group_items = BasketService.filter_group_items_queryset(request.user, basket_id, query)

    paginator = Paginator(group_items, BasketService.BASKET_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    search_params = {'q': query} if query else {}
    search_query_string = urlencode(search_params)

    context = {
        'active_page': 'basket',
        'active_basket': active_basket,
        'basket_groups': basket_groups,
        'page_obj': page_obj,
        'group_total_count': group_total_count,
        'filtered_count': paginator.count,
        'has_results': paginator.count > 0,
        'searched': bool(query),
        'query': query,
        'search_query_string': search_query_string,
        'basket_count': BasketService.count_for_user(request.user),
    }
    return render(request, 'inventory/basket_group_detail.html', context)


# ============================================
# EXTERNAL DB SYNC VIEWS
# ============================================

def _run_sync_worker():
    """Background worker that fetches Part and CarGroup from external DB."""
    import logging
    from django.db import connections

    logger = logging.getLogger('inventory.sync')
    BATCH_SIZE = 5000
    log_lines = []

    def log(msg):
        logger.info(msg)
        log_lines.append(msg)
        _sync_state['log'] = log_lines[:]

    try:
        log('[SYNC] Connecting to external database...')
        ext_conn = connections['external']
        ext_cursor = ext_conn.cursor()
        log('[SYNC] Connected OK.')

        # ── Introspect external parts table columns ──────────────────────────
        log('[SYNC] Introspecting external "parts" table columns...')
        ext_cursor.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'parts'
            ORDER BY ordinal_position
        """)
        ext_parts_cols = ext_cursor.fetchall()
        col_names = [r[0] for r in ext_parts_cols]
        log(f'[SYNC] External parts columns: {col_names}')

        # ── Fetch parts ──────────────────────────────────────────────────────
        log('[SYNC] Fetching ALL valid rows from external parts (in chunks)...')
        ext_cursor.execute(
            "SELECT group_id, brand_id AS brand, code AS part_number FROM parts WHERE code IS NOT NULL AND code != ''"
        )
        columns = [col[0] for col in ext_cursor.description]

        parts_total = 0
        skipped_parts = 0
        
        while True:
            chunk = ext_cursor.fetchmany(10000)
            if not chunk:
                break
            
            rows = [dict(zip(columns, row)) for row in chunk]
            parts_buffer = []
            
            for row in rows:
                pn = str(row.get('part_number', '') or '').strip()
                if not pn:
                    skipped_parts += 1
                    continue
                parts_buffer.append(Part(
                    group_id=str(row.get('group_id', '') or ''),
                    brand=str(row.get('brand', '') or ''),
                    part_number=pn,
                ))
            
            if parts_buffer:
                Part.objects.bulk_create(parts_buffer, batch_size=BATCH_SIZE, ignore_conflicts=True)
                parts_total += len(parts_buffer)
                _sync_state['parts_synced'] = parts_total  # Update status live

        log(f'[SYNC] Parts inserted: {parts_total}, skipped (empty part_number): {skipped_parts}')
        _sync_state['parts_synced'] = parts_total

        # ── Fetch car_groups ─────────────────────────────────────────────────
        log('[SYNC] Introspecting external "car_groups" table columns...')
        ext_cursor.execute("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = 'car_groups'
            ORDER BY ordinal_position
        """)
        cg_cols = [r[0] for r in ext_cursor.fetchall()]
        log(f'[SYNC] External car_groups columns: {cg_cols}')

        log('[SYNC] Fetching ALL rows from external car_groups (in chunks)...')
        ext_cursor.execute('SELECT car_id, group_id FROM car_groups')
        columns = [col[0] for col in ext_cursor.description]

        cg_total = 0
        
        while True:
            chunk = ext_cursor.fetchmany(10000)
            if not chunk:
                break
                
            rows = [dict(zip(columns, row)) for row in chunk]
            cg_buffer = []
            
            for row in rows:
                gid = str(row.get('group_id', '') or '')
                ext_car_id = str(row.get('car_id', '') or '')
                if not ext_car_id:
                    continue
                cg_buffer.append(CarGroup(car_id=ext_car_id, group_id=gid))
                
            if cg_buffer:
                CarGroup.objects.bulk_create(cg_buffer, batch_size=BATCH_SIZE, ignore_conflicts=True)
                cg_total += len(cg_buffer)
                _sync_state['car_groups_synced'] = cg_total  # Update status live

        log(f'[SYNC] CarGroups inserted: {cg_total}')
        _sync_state['car_groups_synced'] = cg_total

        ext_cursor.close()
        ext_conn.close()

        log('[SYNC] Sync completed successfully.')
        _sync_state['status'] = 'completed'

    except Exception as exc:
        _sync_state['status'] = 'failed'
        _sync_state['error'] = str(exc)


@login_required
def sync_external_db_view(request):
    """Page with the external DB sync button."""
    # Reset status if completed, so they can run it again on page load
    if _sync_state['status'] == 'completed':
        _sync_state['status'] = 'idle'

    context = {
        'active_page': 'sync_external',
        'sync_status': _sync_state['status'],
        'parts_synced': _sync_state['parts_synced'],
        'car_groups_synced': _sync_state['car_groups_synced'],
        'sync_error': _sync_state['error'],
        'basket_count': BasketService.count_for_user(request.user),
    }
    return render(request, 'inventory/sync_external.html', context)


@login_required
@require_POST
def trigger_sync_view(request):
    """Trigger the external DB sync (AJAX POST)."""
    with _sync_lock:
        if _sync_state['status'] == 'running':
            return JsonResponse({'ok': False, 'error': 'Sync is already running.'})

        _sync_state['status'] = 'running'
        _sync_state['parts_synced'] = 0
        _sync_state['car_groups_synced'] = 0
        _sync_state['error'] = ''

    thread = threading.Thread(target=_run_sync_worker, daemon=True)
    thread.start()
    return JsonResponse({'ok': True})


@login_required
def sync_status_view(request):
    """AJAX polling endpoint for sync progress."""
    return JsonResponse({
        'status': _sync_state['status'],
        'parts_synced': _sync_state['parts_synced'],
        'car_groups_synced': _sync_state['car_groups_synced'],
        'error': _sync_state['error'],
        'log': _sync_state.get('log', []),
    })
