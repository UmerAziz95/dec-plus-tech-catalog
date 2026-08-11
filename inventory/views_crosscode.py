"""
Cross Code's counterparts to the Cross Car views in views.py: Search Parts
Number, Basket, Edit Parts Numbers (standalone parts catalog), and Brand
Names. Kept in a separate module so views.py doesn't double in size for a
close mirror of existing functionality.
"""
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.views.decorators.http import require_POST
from urllib.parse import urlencode

from .models import BasketCrossCode, BasketItemCrossCode, CarCrossCode, PartCrossCode
from .services.basket_service import BasketService, BasketServiceCrossCode
from .services.brand_names_service import BrandNamesServiceCrossCode
from .services.bulk_search_service import BulkSearchServiceCrossCode
from .services.car_catalog_service import CarCatalogService
from .services.import_services import ExcelImportService
from .services.manual_entry_service import ManualEntryServiceCrossCode
from .services.parts_catalog_service import PartsCatalogServiceCrossCode
from .services.part_number_utils import sanitize_crosscode_search, sanitize_part_number
from .services.part_search_service import PartSearchServiceCrossCode


def _redirect_to_next(request, fallback_view_name):
    next_url = request.POST.get('next', '').strip()
    if next_url.startswith('?'):
        return redirect(f"{reverse(fallback_view_name)}{next_url}")
    if next_url.startswith('/') and not next_url.startswith('//'):
        return redirect(next_url)
    return redirect(fallback_view_name)


# ============================================
# SEARCH PARTS NUMBER
# ============================================

def _complete_bulk_search_crosscode(request, rows_data):
    bulk_results, bulk_summary, error_message = BulkSearchServiceCrossCode.run_bulk_search(
        request.user,
        rows_data,
        save_to_basket=True,
    )
    if error_message:
        messages.error(request, error_message)
        return redirect('inventory:search_part_crosscode')
    request.session['bulk_search_missed_rows_crosscode'] = bulk_summary.get('missed_rows', [])
    request.session['bulk_search_export_rows_crosscode'] = bulk_results
    request.session['bulk_search_summary_crosscode'] = bulk_summary
    messages.success(
        request,
        f'Bulk search complete: {bulk_summary.get("added_to_basket", 0)} item(s) added to basket.',
    )
    return redirect(f"{reverse('inventory:search_part_crosscode')}?bulk_done=1#search-results-panel")


@login_required
def search_part_crosscode_view(request):
    """Cross Code part number search and bulk Excel search on one page."""
    raw_query = request.GET.get('q', '').strip()
    force_exact = request.GET.get('exact') == '1'
    search_query = sanitize_crosscode_search(raw_query, keep_star=True)
    results = []
    total_cars = 0
    total_parts_found = 0
    bulk_results = []
    bulk_has_searched = False
    bulk_summary = None
    did_you_mean = False
    did_you_mean_candidates = []

    if request.GET.get('bulk_done') and request.session.get('bulk_search_export_rows_crosscode') is not None:
        bulk_results = request.session.get('bulk_search_export_rows_crosscode') or []
        bulk_summary = request.session.pop('bulk_search_summary_crosscode', None)
        bulk_has_searched = True

    if request.method == 'POST' and request.FILES.get('excel_file'):
        rows_data, error_message = BulkSearchServiceCrossCode.parse_upload(request.FILES['excel_file'])
        if error_message:
            messages.error(request, error_message)
        elif not rows_data:
            messages.warning(request, 'The uploaded file did not contain any valid rows.')
        else:
            return _complete_bulk_search_crosscode(request, rows_data)

    if search_query and not force_exact:
        needs, candidates, search_query = PartSearchServiceCrossCode.needs_disambiguation(raw_query)
        if needs:
            did_you_mean = True
            did_you_mean_candidates = candidates
        else:
            results = PartSearchServiceCrossCode.build_results(raw_query)
    elif search_query and force_exact:
        results = PartSearchServiceCrossCode.build_results(raw_query)

    if results:
        total_cars = len(results)
        total_parts_found = len({
            part.id
            for item in results
            for part in item['parts']
        })

    page_number = request.GET.get('page', 1)
    paginator = Paginator(results, 25)
    page_obj = paginator.get_page(page_number)

    bulk_missed_rows = request.session.get('bulk_search_missed_rows_crosscode') or []
    bulk_missed_count = len(bulk_missed_rows)

    context = {
        'active_page': 'search_part_crosscode',
        'query': raw_query,
        'search_query': search_query,
        'page_obj': page_obj,
        'total_cars': total_cars,
        'total_parts_found': total_parts_found,
        'has_results': bool(search_query and results),
        'searched': bool(search_query),
        'did_you_mean': did_you_mean,
        'did_you_mean_candidates': did_you_mean_candidates,
        'bulk_results': bulk_results,
        'bulk_has_searched': bulk_has_searched,
        'bulk_result_count': len(bulk_results),
        'bulk_summary': bulk_summary,
        'bulk_missed_count': bulk_missed_count,
        'part_search_export_url': reverse('inventory:part_search_export_crosscode'),
        'part_search_suggestions_url': reverse('inventory:part_search_suggestions_crosscode'),
        'basket_count': BasketService.count_for_user(request.user),
        'basket_count_crosscode': BasketServiceCrossCode.count_for_user(request.user),
    }
    return render(request, 'inventory/crosscode/search_part_crosscode.html', context)


@login_required
def part_search_suggestions_crosscode_view(request):
    query = request.GET.get('q', '')
    return JsonResponse({
        'suggestions': PartSearchServiceCrossCode.suggest_part_numbers(query),
    })


@login_required
def bulk_search_sample_export_crosscode_view(request):
    workbook = BulkSearchServiceCrossCode.build_sample_workbook()
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="bulk_search_sample_crosscode.xlsx"'
    workbook.save(response)
    return response


@login_required
def bulk_search_missed_export_crosscode_view(request):
    missed_rows = request.session.get('bulk_search_missed_rows_crosscode') or []
    if not missed_rows:
        messages.error(request, 'No missed bulk search rows are available to export.')
        return redirect('inventory:search_part_crosscode')

    workbook = BulkSearchServiceCrossCode.build_missed_workbook(missed_rows)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="bulk_search_missed_crosscode.xlsx"'
    workbook.save(response)
    return response


@login_required
def part_search_export_crosscode_view(request):
    raw_query = request.GET.get('q', '').strip()
    search_query = sanitize_part_number(raw_query)
    if not search_query:
        messages.error(request, 'Run a part number search before exporting.')
        return redirect('inventory:search_part_crosscode')

    results = PartSearchServiceCrossCode.build_results(raw_query)
    if not results:
        messages.error(request, 'No vehicles to export for this search.')
        return redirect(f"{reverse('inventory:search_part_crosscode')}?{urlencode({'q': raw_query})}")

    workbook = PartSearchServiceCrossCode.build_export_workbook(raw_query, results)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="part_search_results_crosscode.xlsx"'
    workbook.save(response)
    return response


@login_required
def bulk_search_results_export_crosscode_view(request):
    if 'bulk_search_export_rows_crosscode' not in request.session:
        messages.error(request, 'No bulk search results to export. Upload a file and run bulk search first.')
        return redirect('inventory:search_part_crosscode')

    results = request.session.get('bulk_search_export_rows_crosscode') or []
    workbook = BulkSearchServiceCrossCode.build_results_workbook(results)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="bulk_search_results_crosscode.xlsx"'
    workbook.save(response)
    return response


@login_required
@require_POST
def add_search_results_to_basket_crosscode(request):
    raw_query = request.POST.get('q', '').strip()
    search_query = sanitize_part_number(raw_query)
    brand = request.POST.get('brand', '').strip()
    brand_number = request.POST.get('brand_number', '').strip()
    page = request.POST.get('page', '').strip()

    if not search_query or not brand or not brand_number:
        messages.error(request, 'Search query, brand name, and brand number are required.')
        return redirect('inventory:search_part_crosscode')

    results = PartSearchServiceCrossCode.build_results(raw_query)
    if not results:
        messages.error(request, 'No search results are available to add to the basket.')
        return redirect(f"{reverse('inventory:search_part_crosscode')}?{urlencode({'q': raw_query})}")

    added_count = PartSearchServiceCrossCode.add_results_to_basket(
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
    return redirect(f"{reverse('inventory:search_part_crosscode')}?{urlencode(params)}")


@login_required
@require_POST
def add_to_basket_crosscode(request):
    part_id = request.POST.get('part_id')
    brand = request.POST.get('brand', '').strip()
    brand_number = request.POST.get('brand_number', '').strip()

    if not part_id or not brand or not brand_number:
        messages.error(request, 'Part, brand name, and brand number are required.')
        return redirect(request.META.get('HTTP_REFERER', 'inventory:search_part_crosscode'))

    part = get_object_or_404(PartCrossCode, pk=part_id)

    if BasketServiceCrossCode.item_exists(request.user, part, brand, brand_number):
        messages.warning(
            request,
            f'This item is already in your basket: {brand} | {brand_number} | {part.part_number}'
        )
    else:
        BasketServiceCrossCode.add_item(request.user, part, brand, brand_number)
        messages.success(
            request,
            f'Added to basket: {brand} | {brand_number} → {part.brand} / {part.product_no} / '
            f'{part.oe_brand or "—"} / {part.part_number}'
        )

    referer = request.META.get('HTTP_REFERER', '')
    if referer:
        return redirect(referer)
    return redirect('inventory:search_part_crosscode')


@login_required
@require_POST
def part_update_crosscode_view(request, part_id):
    part = get_object_or_404(PartCrossCode, pk=part_id)
    ok, error = PartsCatalogServiceCrossCode.update_part(part, request.POST)
    if ok:
        messages.success(request, f'Updated part {part.part_number}.')
    else:
        messages.error(request, error)
    return _redirect_to_next(request, 'inventory:search_part_crosscode')


@login_required
@require_POST
def part_delete_crosscode_view(request, part_id):
    part = get_object_or_404(PartCrossCode, pk=part_id)
    part_number = PartsCatalogServiceCrossCode.delete_part(part)
    messages.success(request, f'Deleted part {part_number}. It no longer appears for any vehicle.')
    return _redirect_to_next(request, 'inventory:search_part_crosscode')


@login_required
@require_POST
def cars_crosscode_update_view(request, car_pk):
    car = get_object_or_404(CarCrossCode, pk=car_pk)
    ok, error = CarCatalogService.update_car(car, request.POST)
    if ok:
        messages.success(request, f'Updated car {car.car_id}.')
    else:
        messages.error(request, error)
    return _redirect_to_next(request, 'inventory:search_part_crosscode')


# ============================================
# BASKET
# ============================================

def _basket_redirect_params_crosscode(request):
    query = BasketServiceCrossCode.clean_search_query(request.POST.get('q', ''))
    page = BasketServiceCrossCode.parse_page_number(request.POST.get('page', 1))
    return query, page


def _redirect_to_basket_crosscode(request, user):
    query, page = _basket_redirect_params_crosscode(request)
    total = BasketServiceCrossCode.filter_items_queryset(user, query).count()
    page = BasketServiceCrossCode.page_after_delete(page, total)
    params = BasketServiceCrossCode.list_query_params(query, page)
    url = reverse('inventory:basket_crosscode')
    if params:
        url = f'{url}?{urlencode(params)}'
    return redirect(url)


def _redirect_to_basket_group_crosscode(request, user, basket_id):
    query, page = _basket_redirect_params_crosscode(request)
    total = BasketServiceCrossCode.filter_group_items_queryset(user, basket_id, query).count()
    page = BasketServiceCrossCode.page_after_delete(page, total)
    params = BasketServiceCrossCode.list_query_params(query, page)
    url = reverse('inventory:basket_group_detail_crosscode', kwargs={'basket_id': basket_id})
    if params:
        url = f'{url}?{urlencode(params)}'
    return redirect(url)


@login_required
@require_POST
def remove_from_basket_crosscode(request, basket_id):
    item = get_object_or_404(BasketItemCrossCode, pk=basket_id, user=request.user)
    group_basket_id = item.basket_id
    label = f'{item.basket.brand} | {item.part.part_number}'
    BasketServiceCrossCode.remove_item(item)
    messages.success(request, f'Removed from basket: {label}')

    return_group = request.POST.get('return_group', '').strip()
    if return_group.isdigit() and int(return_group) == group_basket_id:
        if BasketServiceCrossCode.user_owns_basket_group(request.user, int(return_group)):
            return _redirect_to_basket_group_crosscode(request, request.user, int(return_group))
    return _redirect_to_basket_crosscode(request, request.user)


@login_required
@require_POST
def update_basket_item_crosscode(request, basket_id):
    item = get_object_or_404(BasketItemCrossCode, pk=basket_id, user=request.user)

    brand = request.POST.get('brand', '').strip()
    brand_number = request.POST.get('brand_number', '').strip()

    if not brand or not brand_number:
        messages.error(request, 'Brand name and brand number are required.')
        return _redirect_to_basket_crosscode(request, request.user)

    basket, _created = BasketServiceCrossCode.get_or_create_basket(brand, brand_number)
    item.basket = basket
    item.save(update_fields=['basket', 'updated_at'])
    BasketServiceCrossCode.prune_empty_baskets()

    messages.success(request, f'Updated: {brand} | {item.part.part_number}')
    return _redirect_to_basket_crosscode(request, request.user)


@login_required
def basket_export_crosscode_view(request):
    if not BasketItemCrossCode.objects.filter(user=request.user).exists():
        messages.error(request, 'Your basket is empty. Nothing to export.')
        return redirect('inventory:basket_crosscode')

    workbook = BasketServiceCrossCode.build_export_workbook(request.user)
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = 'attachment; filename="basket_crosscode.xlsx"'
    workbook.save(response)
    return response


@login_required
@require_POST
def remove_basket_duplicates_crosscode_view(request):
    removed_count = BasketServiceCrossCode.remove_duplicates(request.user)
    if removed_count:
        messages.success(request, f'Removed {removed_count} duplicate basket item(s).')
    else:
        messages.info(request, 'No duplicate basket rows were found.')
    return redirect('inventory:basket_crosscode')


@login_required
@require_POST
def clear_basket_crosscode_view(request):
    removed_count = BasketServiceCrossCode.clear_basket(request.user)
    if removed_count:
        messages.success(request, f'Cleared {removed_count} item(s) from your basket.')
    else:
        messages.info(request, 'Your basket is already empty.')
    return redirect('inventory:basket_crosscode')


@login_required
def basket_crosscode_view(request):
    query = BasketServiceCrossCode.clean_search_query(request.GET.get('q', ''))
    total_item_count = BasketServiceCrossCode.count_for_user(request.user)
    basket_groups = BasketServiceCrossCode.filter_basket_groups(request.user, query)
    total_group_count = len(BasketServiceCrossCode.get_user_basket_groups(request.user))

    page_number = request.GET.get('page', 1)
    paginator = Paginator(basket_groups, BasketServiceCrossCode.BASKET_PAGE_SIZE)
    page_obj = paginator.get_page(page_number)

    search_params = {'q': query} if query else {}
    search_query_string = urlencode(search_params)

    context = {
        'active_page': 'basket_crosscode',
        'page_obj': page_obj,
        'query': query,
        'filtered_count': paginator.count,
        'total_basket_count': total_group_count,
        'total_item_count': total_item_count,
        'basket_count': BasketService.count_for_user(request.user),
        'basket_count_crosscode': total_item_count,
        'has_results': paginator.count > 0,
        'searched': bool(query),
        'search_query_string': search_query_string,
    }
    return render(request, 'inventory/crosscode/basket_crosscode.html', context)


@login_required
def basket_group_detail_crosscode_view(request, basket_id):
    if not BasketServiceCrossCode.user_owns_basket_group(request.user, basket_id):
        messages.error(request, 'This brand group is not in your basket.')
        return redirect('inventory:basket_crosscode')

    active_basket = get_object_or_404(BasketCrossCode, pk=basket_id)
    basket_groups = BasketServiceCrossCode.get_user_basket_groups(request.user)

    query = BasketServiceCrossCode.clean_search_query(request.GET.get('q', ''))
    group_total_count = BasketServiceCrossCode.get_group_items_queryset(request.user, basket_id).count()
    group_items = BasketServiceCrossCode.filter_group_items_queryset(request.user, basket_id, query)

    paginator = Paginator(group_items, BasketServiceCrossCode.BASKET_PAGE_SIZE)
    page_obj = paginator.get_page(request.GET.get('page', 1))

    search_params = {'q': query} if query else {}
    search_query_string = urlencode(search_params)

    context = {
        'active_page': 'basket_crosscode',
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
        'basket_count_crosscode': BasketServiceCrossCode.count_for_user(request.user),
    }
    return render(request, 'inventory/crosscode/basket_group_detail_crosscode.html', context)


# ============================================
# EDIT PARTS NUMBERS (standalone parts catalog)
# ============================================

@login_required
def parts_catalog_crosscode_view(request):
    query = PartsCatalogServiceCrossCode.clean_text(request.GET.get('q', ''), 255)
    queryset = PartsCatalogServiceCrossCode.build_queryset(query)

    page_number = request.GET.get('page', 1)
    paginator = Paginator(queryset, 25)
    page_obj = paginator.get_page(page_number)

    context = {
        'active_page': 'parts_catalog_crosscode',
        'query': query,
        'page_obj': page_obj,
        'total_parts': paginator.count,
        'has_results': paginator.count > 0,
        'basket_count': BasketService.count_for_user(request.user),
        'basket_count_crosscode': BasketServiceCrossCode.count_for_user(request.user),
    }
    return render(request, 'inventory/crosscode/parts_catalog_crosscode.html', context)


# ============================================
# IMPORT SAMPLE FILES
# ============================================
# Cross Code Excel sample uses Product Brand / Product No / Brand / Code.
# Legacy cars/groups sample endpoints remain for older links.

def _sample_workbook_response(workbook, filename):
    response = HttpResponse(
        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    workbook.save(response)
    return response


@login_required
def cars_crosscode_sample_export_view(request):
    return _sample_workbook_response(ExcelImportService.build_cars_sample_workbook(), 'cars_crosscode_sample.xlsx')


@login_required
def groups_crosscode_sample_export_view(request):
    return _sample_workbook_response(ExcelImportService.build_groups_sample_workbook(), 'groups_crosscode_sample.xlsx')


@login_required
def parts_crosscode_sample_export_view(request):
    return _sample_workbook_response(
        ExcelImportService.build_crosscode_sample_workbook(),
        'Cross_code_example.xlsx',
    )


@login_required
def manual_add_suggestions_crosscode_view(request):
    query = request.GET.get('q', '')
    field = (request.GET.get('field') or 'product_no').strip().lower()
    if field == 'product_brand':
        suggestions = ManualEntryServiceCrossCode.suggest_product_brands(query)
    else:
        suggestions = ManualEntryServiceCrossCode.suggest_product_nos(query)
    return JsonResponse({'suggestions': suggestions})


@login_required
@require_POST
def manual_add_part_crosscode_view(request):
    product_brand = request.POST.get('product_brand', '')
    product_no = request.POST.get('product_no', '')
    oe_brand = request.POST.get('oe_brand', '')
    code = request.POST.get('code', '') or request.POST.get('part_number', '')
    ok, error = ManualEntryServiceCrossCode.add_cross_code_row(
        product_brand, product_no, oe_brand, code,
    )
    if ok:
        messages.success(
            request,
            f'Added Cross Code row: {product_brand.strip()} / {product_no.strip()} / '
            f'{(oe_brand or "—").strip()} / {code.strip()}.',
        )
    else:
        messages.error(request, error)
    return redirect(f"{reverse('inventory:import_data')}?{urlencode({'catalog': 'crosscode'})}")


# ============================================
# BRAND NAMES
# ============================================

@login_required
def brand_names_crosscode_view(request):
    query = request.GET.get('q', '').strip()
    brand_field = (request.GET.get('field') or BrandNamesServiceCrossCode.FIELD_PRODUCT_BRAND).strip().lower()
    if brand_field not in BrandNamesServiceCrossCode.FIELD_MAP:
        brand_field = BrandNamesServiceCrossCode.FIELD_PRODUCT_BRAND
    brands = BrandNamesServiceCrossCode.list_brands(query, field=brand_field)

    context = {
        'active_page': 'brand_names_crosscode',
        'query': query,
        'brand_field': brand_field,
        'brands': brands,
        'has_results': bool(brands),
        'basket_count': BasketService.count_for_user(request.user),
        'basket_count_crosscode': BasketServiceCrossCode.count_for_user(request.user),
    }
    return render(request, 'inventory/crosscode/brand_names_crosscode.html', context)


@login_required
@require_POST
def brand_rename_crosscode_view(request):
    old_name = request.POST.get('old_name', '')
    new_name = request.POST.get('new_name', '')
    brand_field = (request.POST.get('field') or BrandNamesServiceCrossCode.FIELD_PRODUCT_BRAND).strip().lower()
    ok, error, updated_count = BrandNamesServiceCrossCode.rename_brand(
        old_name, new_name, field=brand_field,
    )
    label = 'Product Brand' if brand_field == BrandNamesServiceCrossCode.FIELD_PRODUCT_BRAND else 'Brand'
    if ok:
        messages.success(
            request,
            f'Renamed {label} "{old_name}" to "{new_name}" on {updated_count} Cross Code row(s).',
        )
    else:
        messages.error(request, error)
    redirect_url = reverse('inventory:brand_names_crosscode')
    if brand_field != BrandNamesServiceCrossCode.FIELD_PRODUCT_BRAND:
        redirect_url = f'{redirect_url}?field={brand_field}'
    return redirect(redirect_url)
