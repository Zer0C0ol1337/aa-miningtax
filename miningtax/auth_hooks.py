from allianceauth import hooks
from allianceauth.services.hooks import MenuItemHook, UrlHook
from django.utils.translation import gettext_lazy as _

from . import urls


# Einziger Menüpunkt in der Sidebar — führt zum persönlichen Dashboard
# Abrechnung und Einstellungen sind nur über Buttons im Tool selbst erreichbar
class MiningTaxMenuItem(MenuItemHook):
    def __init__(self):
        MenuItemHook.__init__(
            self,
            _('Mining Tax'),
            'fas fa-cubes fa-fw',
            'miningtax:dashboard',
            navactive=['miningtax:']
        )

    def render(self, request):
        if request.user.is_authenticated and request.user.has_perm('miningtax.basic_access'):
            return MenuItemHook.render(self, request)
        return ''


@hooks.register('menu_item_hook')
def register_menu():
    return MiningTaxMenuItem()


# Bindet unsere urls.py unter dem Pfad /miningtax/ ins Gesamtsystem ein
@hooks.register('url_hook')
def register_urls():
    return UrlHook(urls, 'miningtax', r'^miningtax/')
