from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

SEED_USERS = (
    {
        'email': 'admin@decplus.com',
        'password': 'Admin@123',
        'full_name': 'Admin User',
        'phone': '03001234567',
        'is_staff': True,
        'is_superuser': True,
    },
    {
        'email': 'staff@decplus.com',
        'password': 'Staff@123',
        'full_name': 'Staff User',
        'phone': '03007654321',
        'is_staff': True,
        'is_superuser': False,
    },
    {
        'email': 'user@decplus.com',
        'password': 'User@123',
        'full_name': 'Regular User',
        'phone': '03001112233',
        'is_staff': False,
        'is_superuser': False,
    },
)


class Command(BaseCommand):
    help = 'Seed default admin, staff, and regular users for local testing.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--clear',
            action='store_true',
            help='Delete previously seeded users (emails ending with @decplus.com).',
        )

    def handle(self, *args, **options):
        User = get_user_model()

        if options['clear']:
            deleted, _ = User.objects.filter(email__iendswith='@decplus.com').delete()
            self.stdout.write(self.style.WARNING(f'Removed {deleted} seeded user(s).'))
            return

        created = 0
        updated = 0

        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Seeded users:'))
        self.stdout.write('-' * 56)

        for data in SEED_USERS:
            password = data['password']
            defaults = {
                'full_name': data['full_name'],
                'phone': data['phone'],
                'is_staff': data['is_staff'],
                'is_superuser': data['is_superuser'],
                'is_active': True,
            }
            user, was_created = User.objects.get_or_create(
                email=data['email'],
                defaults=defaults,
            )
            user.full_name = data['full_name']
            user.phone = data['phone']
            user.is_staff = data['is_staff']
            user.is_superuser = data['is_superuser']
            user.is_active = True
            user.set_password(password)
            user.save()

            if was_created:
                created += 1
                action = 'created'
            else:
                updated += 1
                action = 'updated'

            role = 'superuser' if user.is_superuser else ('staff' if user.is_staff else 'user')
            self.stdout.write(f'  [{action}] {user.email}  |  password: {password}  |  role: {role}')

        self.stdout.write('-' * 56)
        self.stdout.write(self.style.SUCCESS(
            f'Done: {created} created, {updated} updated.'
        ))
        self.stdout.write('Login at /login/ with any of the emails above.')
