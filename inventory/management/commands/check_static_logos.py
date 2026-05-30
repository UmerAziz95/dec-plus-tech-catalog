from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Verify brand logo PNG files exist before collectstatic.'

    def handle(self, *args, **options):
        rel_paths = (
            'images/dec-catalog-logo.png',
        )
        roots = [
            settings.BASE_DIR / 'static',
            settings.BASE_DIR / 'accounts' / 'static',
        ]
        missing = []
        for rel in rel_paths:
            found = [p for root in roots if (p := root / rel).is_file()]
            if found:
                self.stdout.write(self.style.SUCCESS(f'OK  {rel} -> {found[0]}'))
            else:
                missing.append(rel)
                self.stdout.write(self.style.ERROR(f'MISSING  {rel}'))

        collected = settings.STATIC_ROOT / 'images' / 'dec-catalog-logo.png'
        if collected.is_file():
            self.stdout.write(self.style.SUCCESS(f'OK  collected -> {collected}'))
        else:
            self.stdout.write(
                self.style.WARNING(
                    f'Not in STATIC_ROOT yet: {collected} (run collectstatic)'
                )
            )

        if missing:
            raise SystemExit(1)
