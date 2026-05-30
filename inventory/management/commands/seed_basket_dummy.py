from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from inventory.models import BasketItem
from inventory.services.basket_service import BasketService
from inventory.services.part_search_service import PartSearchService

DEFAULT_PART = 'MQ901069'
DEFAULT_BRAND = 'Kanoya'
CROSS_CODES = ('P12X35', 'T57G60', 'F44X21')


class Command(BaseCommand):
    help = (
        'Add dummy basket rows for one part number under several cross codes '
        '(for testing basket search and multi-code display).'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--email',
            type=str,
            default='',
            help='User email (default: first user in database)',
        )
        parser.add_argument(
            '--part-number',
            type=str,
            default=DEFAULT_PART,
            help=f'OE part number (default: {DEFAULT_PART})',
        )
        parser.add_argument(
            '--brand',
            type=str,
            default=DEFAULT_BRAND,
            help=f'Cross brand name (default: {DEFAULT_BRAND})',
        )
        parser.add_argument(
            '--per-code',
            type=int,
            default=8,
            help='How many vehicles to add per cross code (default: 8; use 0 for all catalog matches)',
        )
        parser.add_argument(
            '--clear-part',
            action='store_true',
            help='Remove existing basket lines for this part number for this user first',
        )

    def handle(self, *args, **options):
        User = get_user_model()
        email = (options['email'] or '').strip()
        if email:
            user = User.objects.filter(email__iexact=email).first()
        else:
            user = User.objects.order_by('id').first()
        if not user:
            self.stdout.write(self.style.ERROR('No user found.'))
            return

        part_number = (options['part_number'] or '').strip()
        brand = (options['brand'] or DEFAULT_BRAND).strip()
        per_code = options['per_code']
        if per_code < 0:
            per_code = 0

        results = PartSearchService.build_results(part_number)
        if not results:
            self.stdout.write(self.style.ERROR(
                f'Part "{part_number}" not found in catalog. Import parts or pick another --part-number.'
            ))
            return

        car_part_pairs = []
        for item in results:
            car = item['car']
            for part in item['parts']:
                car_part_pairs.append((car, part))
                break

        if not car_part_pairs:
            self.stdout.write(self.style.ERROR('No car/part pairs in catalog for this part.'))
            return

        with transaction.atomic():
            if options['clear_part']:
                deleted, _ = BasketItem.objects.filter(
                    user=user,
                    part__part_number__iexact=part_number,
                ).delete()
                BasketService.prune_empty_baskets()
                self.stdout.write(f'Removed {deleted} existing line(s) for {part_number}.')

            created_total = 0
            for cross_code in CROSS_CODES:
                basket, _ = BasketService.get_or_create_basket(brand, cross_code)
                limit = len(car_part_pairs) if per_code == 0 else min(per_code, len(car_part_pairs))
                code_created = 0
                for car, part in car_part_pairs[:limit]:
                    _, created = BasketItem.objects.get_or_create(
                        user=user,
                        car=car,
                        part=part,
                        basket=basket,
                    )
                    if created:
                        code_created += 1
                        created_total += 1
                self.stdout.write(
                    f'  {cross_code}: {code_created} new line(s) '
                    f'({limit} vehicles targeted)'
                )

        total_for_part = BasketItem.objects.filter(
            user=user,
            part__part_number__iexact=part_number,
        ).count()
        self.stdout.write(self.style.SUCCESS(''))
        self.stdout.write(self.style.SUCCESS(
            f'Done for user {user.email}. Created {created_total} new basket line(s).'
        ))
        self.stdout.write(f'Total basket lines for {part_number}: {total_for_part}')
        self.stdout.write('')
        self.stdout.write('Test in the app:')
        self.stdout.write(f'  Basket search: {part_number}')
        self.stdout.write(f'  Or cross codes: {", ".join(CROSS_CODES)}')
