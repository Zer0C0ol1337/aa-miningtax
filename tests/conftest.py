"""
Test setup: plain Django with an in-memory SQLite database — no Alliance Auth,
Celery or ESI needed. The tests cover the plugin's pure logic (payment codes,
rounding, formatting, migrations); code that needs Alliance Auth is loaded
function by function through helpers.load() with stand-ins for its imports.
"""
import pathlib
import sys

import django
from django.conf import settings

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def pytest_configure():
    # Configures Django once for the whole test run.
    settings.configure(
        USE_TZ=True,
        USE_I18N=True,
        INSTALLED_APPS=['tests.fake_app'],
        DATABASES={'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}},
        CACHES={'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache'}},
        TEMPLATES=[{
            'BACKEND': 'django.template.backends.django.DjangoTemplates',
            'OPTIONS': {'libraries': {'miningtax_tags': 'miningtax.templatetags.miningtax_tags'}},
        }],
    )
    django.setup()
