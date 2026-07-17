from django.core.management.base import BaseCommand

from miningtax.models import MiningLedgerEntry


class Command(BaseCommand):
    help = (
        'One-off repair: corrects stored ore names on existing mining ledger '
        'entries using eveuniverse (authoritative, from ESI). Fixes entries that '
        'were synced with a wrong name from a previously mis-seeded OreCategory '
        'table. Missing ore types are loaded from ESI on the fly. Tax categories '
        'are resolved by type_id and are unaffected — this only repairs display '
        'names.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run', action='store_true',
            help='Show what would change without writing to the database.',
        )

    def handle(self, *args, **options):
        try:
            from eveuniverse.models import EveType
        except ImportError:
            self.stderr.write(self.style.ERROR(
                'django-eveuniverse is not installed — cannot repair names.'
            ))
            return

        dry = options['dry_run']
        type_ids = list(
            MiningLedgerEntry.objects.values_list('type_id', flat=True).distinct()
        )
        self.stdout.write(f'Checking {len(type_ids)} distinct ore types...')

        name_map = {}
        loaded = 0
        for tid in type_ids:
            et = EveType.objects.filter(id=tid).first()
            if et is None:
                # Not loaded yet — pull it from ESI (synchronous, no Celery).
                try:
                    et, _ = EveType.objects.update_or_create_esi(id=tid)
                    loaded += 1
                except Exception as e:
                    self.stderr.write(self.style.WARNING(f'  type {tid} could not be loaded: {e}'))
                    continue
            if et and et.name:
                name_map[tid] = et.name

        if loaded:
            self.stdout.write(f'Loaded {loaded} missing ore type(s) from ESI.')

        fixed = 0
        for tid, correct_name in name_map.items():
            qs = MiningLedgerEntry.objects.filter(type_id=tid).exclude(type_name=correct_name)
            count = qs.count()
            if not count:
                continue
            wrong_names = set(qs.values_list('type_name', flat=True))
            sample = ', '.join(sorted(wrong_names)[:3])
            self.stdout.write(f"  {tid}: {count} entry(ies) '{sample}' → '{correct_name}'")
            if not dry:
                qs.update(type_name=correct_name)
            fixed += count

        if dry:
            self.stdout.write(self.style.WARNING(
                f'DRY RUN — {fixed} entry(ies) would be renamed. Re-run without --dry-run to apply.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS(
                f'✅ Repaired {fixed} ledger entry name(s).'
            ))