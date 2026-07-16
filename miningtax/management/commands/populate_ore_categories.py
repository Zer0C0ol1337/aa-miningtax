from django.core.management.base import BaseCommand
from miningtax.models import OreCategory


# Erz-Kategorien für EVE Online Mining Tax
# type_id: ESI type_id des Erzes
# type_name: Name des Erzes
# category: R4/R8/R16/R32/R64/Ice/Ore/Mercoxit

ORE_DATA = [
    # R4 Monde
    (45494, 'Cobaltite', 'R4'),
    (45495, 'Euxenite', 'R4'),
    (45496, 'Titanite', 'R4'),
    (45497, 'Scheelite', 'R4'),
    # R8 Monde
    (45498, 'Otavite', 'R8'),
    (45499, 'Sperrylite', 'R8'),
    (45500, 'Vanadinite', 'R8'),
    (45501, 'Chromite', 'R8'),
    # R16 Monde
    (45502, 'Carnotite', 'R16'),
    (45503, 'Zircon', 'R16'),
    (45504, 'Pollucite', 'R16'),
    (45506, 'Cinnabar', 'R16'),
    # R32 Monde
    (45510, 'Xenotime', 'R32'),
    (45511, 'Monazite', 'R32'),
    (45512, 'Loparite', 'R32'),
    (45513, 'Ytterbite', 'R32'),
    # R64 Monde
    (45490, 'Thulium Hafnite', 'R64'),
    (45491, 'Promethium Mercurite', 'R64'),
    (45492, 'Neo Mercurite', 'R64'),
    (45493, 'Dysprosium Hafnite', 'R64'),
    # Ice
    (16262, 'Blue Ice', 'Ice'),
    (16263, 'Clear Icicle', 'Ice'),
    (16264, 'Glacial Mass', 'Ice'),
    (16265, 'White Glaze', 'Ice'),
    (16266, 'Thick Blue Ice', 'Ice'),
    (16267, 'Pristine White Glaze', 'Ice'),
    (16268, 'Smooth Glacial Mass', 'Ice'),
    (16269, 'Enriched Clear Icicle', 'Ice'),
    (28627, 'Glare Crust', 'Ice'),
    (28628, 'Dark Glitter', 'Ice'),
    (28629, 'Gelidus', 'Ice'),
    (28630, 'Krystallos', 'Ice'),
    # Normales Erz (Ore)
    (1228, 'Veldspar', 'Ore'),
    (1230, 'Scordite', 'Ore'),
    (1224, 'Pyroxeres', 'Ore'),
    (18, 'Plagioclase', 'Ore'),
    (1227, 'Omber', 'Ore'),
    (1226, 'Kernite', 'Ore'),
    (20, 'Jaspet', 'Ore'),
    (1229, 'Hemorphite', 'Ore'),
    (1231, 'Hedbergite', 'Ore'),
    (21, 'Gneiss', 'Ore'),
    (1225, 'Dark Ochre', 'Ore'),
    (22, 'Spodumain', 'Ore'),
    (1232, 'Crokite', 'Ore'),
    (19, 'Bistot', 'Ore'),
    (1223, 'Arkonor', 'Ore'),
    # Mercoxit — own tax category (own editable rate), not lumped in with 'Ore'
    (11396, 'Mercoxit', 'Mercoxit'),
    # Abyssal Ore
    (46676, 'Bezdnacine', 'Ore'),
    (46678, 'Rakovene', 'Ore'),
    (46679, 'Talassonite', 'Ore'),
]


class Command(BaseCommand):
    help = 'Befüllt die OreCategory Tabelle mit EVE Online Erz-Kategorien'

    def handle(self, *args, **options):
        created = 0
        updated = 0

        for type_id, type_name, category in ORE_DATA:
            obj, was_created = OreCategory.objects.update_or_create(
                type_id=type_id,
                defaults={
                    'type_name': type_name,
                    'category': category,
                }
            )
            if was_created:
                created += 1
            else:
                updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'✅ OreCategory befüllt: {created} neu angelegt, {updated} aktualisiert'
            )
        )