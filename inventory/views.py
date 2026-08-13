from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.paginator import Paginator
from django.db import connection, transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.urls import reverse
from django.views.decorators.http import require_POST
from urllib.parse import urlencode

from .forms import ExcelImportForm
from .models import (
    Car, CarCrossCode, CarGroup, CarGroupCrossCode, Part, PartCrossCode,
    Basket, BasketItem, ImportBatch,
)
from .services.basket_service import BasketService, BasketServiceCrossCode
from .services.bulk_search_service import BulkSearchService
from .services.car_catalog_service import CarCatalogService
from .services.import_services import ExcelImportService, ImportBatchDeleteService
from .services.manual_entry_service import ManualEntryService
from .services.part_number_utils import sanitize_part_number
from .services.part_search_service import PartSearchService



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


def _get_approx_count(table_name):
    """
    Fast row estimate via pg_class.reltuples. When the estimate is 0
    (common for small or newly filled tables), fall back to COUNT(*).
    """
    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute("SELECT reltuples::bigint FROM pg_class WHERE relname = %s", [table_name])
        row = cursor.fetchone()
        if row is None:
            return 0  # table doesn't exist at all
        approx = int(row[0] or 0)
        if approx > 0:
            return approx
        # reltuples is a planner estimate refreshed by ANALYZE/autovacuum —
        # brand-new or lightly-populated tables can sit at 0 after real rows
        # exist. Exact COUNT(*) is fine here for those cases.
        cursor.execute(f'SELECT COUNT(*) FROM "{table_name}"')  # noqa: S608 - table_name is always a fixed literal
        row = cursor.fetchone()
        return int(row[0]) if row else 0


def _get_distinct_count(model, field, exclude_blank=True):
    qs = model.objects.all()
    if exclude_blank:
        qs = qs.exclude(**{field: ''})
    return qs.values(field).distinct().count()


def _get_unmapped_table_stats():
    """
    Discover any DB tables that aren't backed by a registered Django model
    (e.g. tables created by an external sync or manual import) and report
    their approximate row counts, so the dashboard can surface them without
    code changes each time a new table shows up.
    """
    from django.apps import apps
    from django.db import connection

    # include_auto_created picks up Django's M2M "through" tables (e.g.
    # accounts_user_groups); django_migrations is managed outside the app
    # registry entirely, so it's excluded explicitly.
    known_tables = {model._meta.db_table for model in apps.get_models(include_auto_created=True)}
    known_tables.add('django_migrations')

    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT c.relname, c.reltuples::bigint
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relkind = 'r'
            ORDER BY c.relname
        """)
        rows = cursor.fetchall()

    extra_tables = []
    for table_name, approx_count in rows:
        if table_name in known_tables:
            continue
        count = max(int(approx_count or 0), 0)
        if count == 0:
            count = _get_approx_count(table_name)
        extra_tables.append({
            'table_name': table_name,
            'label': table_name.replace('_', ' ').title(),
            'count': count,
        })
    return extra_tables


# Registry the dashboard's stat table loops over. Adding a future module
# (a 3rd catalog alongside Cross Car / Cross Code) means adding one entry
# here — the view and template render it automatically, no hardcoded block
# to duplicate.
def _build_cross_car_stats(user):
    return [
        {'label': 'Total Cars', 'value': _get_approx_count('cars'), 'tone': 'purple', 'icon': 'car'},
        {'label': 'Total Parts', 'value': _get_approx_count('parts'), 'tone': 'blue', 'icon': 'parts'},
        {'label': 'Car–Group Links', 'value': _get_approx_count('car_groups'), 'tone': 'gray', 'icon': 'db'},
        {'label': 'Basket Items', 'value': BasketService.count_for_user(user), 'tone': 'amber', 'icon': 'basket'},
    ]


def _build_cross_code_stats(user):
    # Flat Product Brand / Product No / Brand / Code catalog — no cars.
    return [
        {'label': 'Total Rows', 'value': _get_approx_count('parts_crosscode'), 'tone': 'blue', 'icon': 'parts'},
        {'label': 'Product Brands', 'value': _get_distinct_count(PartCrossCode, 'brand'), 'tone': 'purple', 'icon': 'brand'},
        {'label': 'Unique Codes', 'value': _get_distinct_count(PartCrossCode, 'part_number'), 'tone': 'gray', 'icon': 'db'},
        {'label': 'Basket Items', 'value': BasketServiceCrossCode.count_for_user(user), 'tone': 'amber', 'icon': 'basket'},
    ]


DASHBOARD_MODULES = [
    {
        'key': 'cross_car',
        'label': 'Cross Car',
        'stats_builder': _build_cross_car_stats,
    },
    {
        'key': 'cross_code',
        'label': 'Cross Code',
        'stats_builder': _build_cross_code_stats,
    },
]


@login_required
def dashboard_view(request):
    """Main dashboard with database statistics."""
    modules = []
    for cfg in DASHBOARD_MODULES:
        modules.append({
            'key': cfg['key'],
            'label': cfg['label'],
            'stats': cfg['stats_builder'](request.user),
        })

    context = {
        'active_page': 'dashboard',
        'modules': modules,
        'extra_tables': _get_unmapped_table_stats(),
        # Kept for the sidebar badges and quick-action cards, which aren't
        # part of the generic stat-table loop.
        'basket_count': BasketService.count_for_user(request.user),
        'basket_count_crosscode': BasketServiceCrossCode.count_for_user(request.user),
    }
    return render(request, 'inventory/dashboard.html', context)


_CROSSCODE_IMPORT_TYPES = {
    ImportBatch.TYPE_CARS_CROSSCODE,
    ImportBatch.TYPE_GROUPS_CROSSCODE,
    ImportBatch.TYPE_PARTS_CROSSCODE,
}


def _import_catalog_from_request(request, import_type=None, active_batch=None):
    """Which sidebar Import Data entry owns this page: 'cars' or 'crosscode'."""
    catalog = (request.GET.get('catalog') or request.POST.get('catalog') or '').strip().lower()
    if catalog in ('cars', 'crosscode'):
        return catalog
    if import_type in _CROSSCODE_IMPORT_TYPES:
        return 'crosscode'
    if active_batch and active_batch.import_type in _CROSSCODE_IMPORT_TYPES:
        return 'crosscode'
    return 'cars'


def _import_data_url(catalog='cars', batch_id=None, fragment=None):
    params = {'catalog': catalog}
    if batch_id is not None:
        params['batch'] = batch_id
    url = f"{reverse('inventory:import_data')}?{urlencode(params)}"
    if fragment:
        url = f"{url}#{fragment}"
    return url


@login_required
def import_data_view(request):
    active_batch = None
    form = ExcelImportForm(initial={'import_type': ImportBatch.TYPE_CARS})
    if request.method == 'POST':
        form = ExcelImportForm(request.POST, request.FILES)
        if form.is_valid():
            import_type = form.cleaned_data['import_type']
            batch_id = ExcelImportService.enqueue(
                form.cleaned_data['excel_file'],
                request.user,
                import_type,
            )
            messages.success(
                request,
                'Import started in the background. This page will show progress; you can leave and come back.',
            )
            catalog = 'crosscode' if import_type in _CROSSCODE_IMPORT_TYPES else 'cars'
            return redirect(_import_data_url(catalog=catalog, batch_id=batch_id))
        messages.error(request, 'Please fix validation errors and try again.')
    batch_id = request.GET.get('batch')
    if batch_id and str(batch_id).isdigit():
        active_batch = ImportBatch.objects.filter(
            pk=int(batch_id),
            uploaded_by=request.user,
        ).first()
    catalog = _import_catalog_from_request(request, active_batch=active_batch)
    history_limit = 50
    user_batches = ImportBatch.objects.filter(uploaded_by=request.user)
    catalog_types = (
        ImportBatch.CROSSCODE_CATALOG_TYPES
        if catalog == 'crosscode'
        else ImportBatch.CARS_CATALOG_TYPES
    )
    file_import_history = list(
        user_batches.filter(import_type__in=catalog_types).order_by('-created_at')[:history_limit]
    )
    context = {
        'active_page': 'import_data_crosscode' if catalog == 'crosscode' else 'import_data',
        'import_catalog': catalog,
        'form': form,
        'active_batch': active_batch,
        'import_types': ImportBatch.IMPORT_TYPE_CHOICES,
        'file_import_history': file_import_history,
        'basket_count': BasketService.count_for_user(request.user),
        'basket_count_crosscode': BasketServiceCrossCode.count_for_user(request.user),
        'max_upload_size_bytes': getattr(settings, 'IMPORT_MAX_UPLOAD_SIZE_BYTES', 5 * 1024 * 1024 * 1024),
        'max_upload_size_gb': getattr(settings, 'IMPORT_MAX_UPLOAD_SIZE_GB', 5),
        'import_groups_column_pairs': getattr(settings, 'IMPORT_GROUPS_COLUMN_PAIRS', 3),
        'import_parts_column_blocks': getattr(settings, 'IMPORT_PARTS_COLUMN_BLOCKS', 6),
        'import_history_url': reverse('inventory:import_history'),
    }
    return render(request, 'inventory/import_data.html', context)


@login_required
def import_history_view(request):
    """JSON refresh for the File import history table (catalog-scoped)."""
    history_limit = 50
    catalog = (request.GET.get('catalog') or 'cars').strip().lower()
    if catalog not in ('cars', 'crosscode'):
        catalog = 'cars'
    catalog_types = (
        ImportBatch.CROSSCODE_CATALOG_TYPES
        if catalog == 'crosscode'
        else ImportBatch.CARS_CATALOG_TYPES
    )
    history = list(
        ImportBatch.objects.filter(
            uploaded_by=request.user,
            import_type__in=catalog_types,
        ).order_by('-created_at')[:history_limit]
    )
    table_html = render_to_string(
        'inventory/partials/import_file_history_rows.html',
        {
            'file_import_history': history,
            'import_catalog': catalog,
        },
        request=request,
    )
    poll = any(
        item.status in (ImportBatch.STATUS_PENDING, ImportBatch.STATUS_PROCESSING)
        for item in history
    )
    return JsonResponse({
        'table_html': table_html,
        'poll': poll,
        'catalog': catalog,
        'count': len(history),
    })


def _sample_workbook_response(workbook, filename):
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


@login_required
def cars_sample_export_view(request):
    return _sample_workbook_response(ExcelImportService.build_cars_sample_workbook(), 'cars_sample.xlsx')


@login_required
def groups_sample_export_view(request):
    return _sample_workbook_response(ExcelImportService.build_groups_sample_workbook(), 'groups_sample.xlsx')


@login_required
def parts_sample_export_view(request):
    return _sample_workbook_response(ExcelImportService.build_parts_sample_workbook(), 'parts_sample.xlsx')


@login_required
def car_with_parts_sample_export_view(request):
    return _sample_workbook_response(ExcelImportService.build_car_with_parts_sample_workbook(), 'car_with_parts_sample.xlsx')


@login_required
def manual_add_suggestions_view(request):
    query = request.GET.get('q', '')
    return JsonResponse({'suggestions': ManualEntryService.suggest_car_models(query)})


@login_required
@require_POST
def manual_add_part_view(request):
    car_model = request.POST.get('car_model', '')
    part_number = request.POST.get('part_number', '')
    ok, error = ManualEntryService.add_part_to_car(car_model, part_number)
    if ok:
        messages.success(request, f'Added part "{part_number.strip()}" to "{car_model.strip()}".')
    else:
        messages.error(request, error)
    return redirect(_import_data_url(catalog='cars'))


@login_required
@require_POST
def import_batch_delete_view(request, batch_id):
    batch = get_object_or_404(ImportBatch, pk=batch_id, uploaded_by=request.user)
    catalog = _import_catalog_from_request(request, import_type=batch.import_type)

    if batch.status in (ImportBatch.STATUS_PENDING, ImportBatch.STATUS_PROCESSING):
        messages.error(
            request,
            'This import is still running or already being deleted — wait for it to finish.',
        )
        return redirect(_import_data_url(catalog=catalog))

    # Large imports (millions of rows) must delete in background chunks.
    # A single SQL DELETE can hit PostgreSQL's 1GB alloc limit.
    try:
        ImportBatchDeleteService.enqueue(batch.id)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(_import_data_url(catalog=catalog))

    messages.success(
        request,
        f'Delete started for "{batch.original_file_name}". '
        f'Large files are removed in the background — keep this page open to watch progress.',
    )
    return redirect(_import_data_url(catalog=catalog, batch_id=batch.id))


@login_required
def import_batch_status_view(request, batch_id):
    batch = ImportBatch.objects.filter(pk=batch_id, uploaded_by=request.user).first()
    if not batch:
        # Batch removed after a background delete finished.
        return JsonResponse({
            'status': True,
            'data': {
                'batch_id': batch_id,
                'import_type': '',
                'original_file_name': '',
                'job_status': ImportBatch.STATUS_COMPLETED,
                'progress_note': 'Delete finished — this import was removed.',
                'failure_reason': '',
                'total_rows': 0,
                'cars_count': 0,
                'groups_count': 0,
                'links_count': 0,
                'parts_count': 0,
                'error_count': 0,
                'completed_at': None,
            },
            'errors': [],
            'deleted': True,
        })

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

    parts_without_vehicles = False
    if search_query:
        results = PartSearchService.build_results(raw_query)
        total_cars = len(results)
        total_parts_found = len({
            part.id
            for item in results
            for part in item['parts']
        })
        if not results:
            parts_without_vehicles = PartSearchService.matching_parts_exist(raw_query)

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
        'parts_without_vehicles': parts_without_vehicles,
        'bulk_results': bulk_results,
        'bulk_has_searched': bulk_has_searched,
        'bulk_result_count': len(bulk_results),
        'bulk_summary': bulk_summary,
        'bulk_missed_count': bulk_missed_count,
        'part_search_export_url': reverse('inventory:part_search_export'),
        'part_search_suggestions_url': reverse('inventory:part_search_suggestions'),
        'basket_count': BasketService.count_for_user(request.user),
    }
    return render(request, 'inventory/search_part.html', context)


@login_required
def part_search_suggestions_view(request):
    query = request.GET.get('q', '')
    return JsonResponse({
        'suggestions': PartSearchService.suggest_part_numbers(query),
    })


@login_required
def bulk_search_sample_export_view(request):
    workbook = BulkSearchService.build_sample_workbook()
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="cross_cars_bulk_search_file.xlsx"'
    workbook.save(response)
    return response


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
    if ok:
        messages.success(request, f'Updated car {car.car_id}.')
    else:
        messages.error(request, error)
    return _redirect_to_next(request, 'inventory:cars_catalog')


def _redirect_to_next(request, fallback_view_name):
    next_url = request.POST.get('next', '').strip()
    if next_url.startswith('?'):
        return redirect(f"{reverse(fallback_view_name)}{next_url}")
    if next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect(fallback_view_name)


@login_required
@require_POST
def part_update_view(request, part_id):
    part = get_object_or_404(Part, pk=part_id)
    part_number = CarCatalogService.clean_text(request.POST.get('part_number', ''), 100)
    brand = CarCatalogService.clean_text(request.POST.get('brand', ''), 100)
    if not part_number:
        messages.error(request, 'Part number is required.')
    else:
        part.part_number = part_number
        part.brand = brand
        part.save(update_fields=['part_number', 'brand', 'updated_at'])
        messages.success(request, f'Updated part {part_number}.')
    return _redirect_to_next(request, 'inventory:search_part')


@login_required
@require_POST
def part_delete_view(request, part_id):
    part = get_object_or_404(Part, pk=part_id)
    part_number = part.part_number
    part.delete()
    messages.success(request, f'Deleted part {part_number}. It no longer appears for any vehicle.')
    return _redirect_to_next(request, 'inventory:search_part')


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


