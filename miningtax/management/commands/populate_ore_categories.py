from django.core.management.base import BaseCommand

from miningtax.models import OreCategory


# Ore tax categories are derived from EVE's own asteroid groups (eve_category 25)
# via eveuniverse, instead of a hand-maintained type_id list. This keeps type_ids
# authoritative (straight from ESI) and automatically covers every ore variant —
# base ores, II/III/IV-Grade, Bountiful/Shining moon-ore tiers, Compressed ice —
# without ever drifting out of sync.
#
# Moon ore rarity groups map directly onto the R-value tax tiers:
GROUP_CATEGORY = {
    1884: 'R4',    # Ubiquitous Moon Asteroids
    1920: 'R8',    # Common Moon Asteroids
    1921: 'R16',   # Uncommon Moon Asteroids
    1922: 'R32',   # Rare Moon Asteroids
    1923: 'R64',   # Exceptional Moon Asteroids
    465:  'Ice',   # Ice
    468:  'Mercoxit',  # Mercoxit
}

# All remaining asteroid groups (Veldspar, Scordite, Arkonor, …) are ordinary
# ore. Listing them explicitly guards against miscategorising anything unexpected
# in eve_category 25 as "Ore".
ORE_GROUPS = {
    450, 451, 452, 453, 454, 455, 456, 457, 458, 459, 460, 461, 462, 467, 469,
}


class Command(BaseCommand):
    help = (
        'Populates the OreCategory table from eveuniverse asteroid groups '
        '(authoritative type_ids from ESI). Requires django-eveuniverse with '
        'asteroid types loaded (see populate_ore_reprocessing / '
        'eveuniverse_load_types --category_id 25).'
    )

    def handle(self, *args, **options):
        try:
            from eveuniverse.models import EveType
        except ImportError:
            self.stderr.write(self.style.ERROR(
                'django-eveuniverse is not installed. Install it and load asteroid '
                'types first: python manage.py eveuniverse_load_types miningtax '
                '--category_id 25'
            ))
            return

        group_to_category = dict(GROUP_CATEGORY)
        for gid in ORE_GROUPS:
            group_to_category[gid] = 'Ore'

        # Only real, published asteroid types in the known ore/moon/ice groups.
        types = EveType.objects.filter(
            eve_group_id__in=group_to_category.keys(),
            published=True,
        ).values('id', 'name', 'eve_group_id')

        if not types:
            self.stderr.write(self.style.WARNING(
                'No asteroid types found in eveuniverse. Load them first with: '
                'python manage.py eveuniverse_load_types miningtax --category_id 25'
            ))
            return

        created = 0
        updated = 0
        for t in types:
            category = group_to_category.get(t['eve_group_id'])
            if not category:
                continue
            obj, was_created = OreCategory.objects.update_or_create(
                type_id=t['id'],
                defaults={'type_name': t['name'], 'category': category},
            )
            if was_created:
                created += 1
            else:
                updated += 1

        by_cat = {}
        for t in types:
            cat = group_to_category.get(t['eve_group_id'])
            by_cat[cat] = by_cat.get(cat, 0) + 1

        self.stdout.write(self.style.SUCCESS(
            f'✅ OreCategory populated from eveuniverse: {created} created, {updated} updated'
        ))
        for cat in ['Ore', 'Ice', 'Mercoxit', 'R4', 'R8', 'R16', 'R32', 'R64']:
            if cat in by_cat:
                self.stdout.write(f'   {cat}: {by_cat[cat]} types')