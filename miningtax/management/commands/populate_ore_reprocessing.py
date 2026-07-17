from django.core.management.base import BaseCommand

from miningtax.models import OreCategory


class Command(BaseCommand):
    help = (
        'Loads reprocessing recipes (type materials) for all known ore types into '
        'eveuniverse, synchronously — no Celery worker required. Run once after '
        'enabling Janice refined pricing, and again whenever new ore types are added '
        'to OreCategory. Safe to re-run (idempotent).'
    )

    def handle(self, *args, **options):
        try:
            from eveuniverse.models import EveType, EveTypeMaterial
        except ImportError:
            self.stderr.write(self.style.ERROR(
                'eveuniverse is not installed. Install django-eveuniverse and set '
                'EVEUNIVERSE_LOAD_TYPE_MATERIALS = True in local.py first.'
            ))
            return

        type_ids = list(OreCategory.objects.values_list('type_id', flat=True))
        if not type_ids:
            self.stderr.write(self.style.WARNING(
                'OreCategory is empty — run populate_ore_categories first.'
            ))
            return

        self.stdout.write(f'Loading reprocessing data for {len(type_ids)} ore types (synchronous)...')

        loaded = 0
        failed = 0
        for tid in type_ids:
            try:
                # Load the ore type WITH its type materials (the reprocessing recipe).
                # This pulls the recipe synchronously; the referenced mineral types
                # are created as stub EveTypes automatically.
                EveType.objects.update_or_create_esi(
                    id=tid,
                    enabled_sections=[EveType.Section.TYPE_MATERIALS],
                )
                loaded += 1
            except Exception as e:
                failed += 1
                self.stderr.write(self.style.WARNING(f'  type {tid} failed: {e}'))

        total_materials = EveTypeMaterial.objects.filter(eve_type_id__in=type_ids).count()

        self.stdout.write(self.style.SUCCESS(
            f'✅ Reprocessing data loaded: {loaded} ore types ok, {failed} failed, '
            f'{total_materials} material rows total.'
        ))
        self.stdout.write(
            'Mineral prices for these materials are fetched live from Janice at '
            'price-update time — nothing else to load.'
        )