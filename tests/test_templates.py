"""Every plugin template compiles, and the pages put the navigation into
Alliance Auth's top bar instead of a button row in the page."""
import pathlib

import pytest
from django.template import engines

TEMPLATES = pathlib.Path(__file__).resolve().parent.parent / 'miningtax' / 'templates' / 'miningtax'
PAGES = ['dashboard.html', 'alliance_overview.html', 'pilot_detail.html', 'settings.html']


@pytest.mark.parametrize('name', sorted(p.name for p in TEMPLATES.glob('*.html')))
def test_template_compiles(name):
    # Compiling checks tag syntax and block structure without rendering, so
    # Alliance Auth's base template is not needed.
    engines['django'].from_string(TEMPLATES.joinpath(name).read_text(encoding='utf-8'))


@pytest.mark.parametrize('name', PAGES)
def test_page_uses_the_top_bar(name):
    source = TEMPLATES.joinpath(name).read_text(encoding='utf-8')
    nav_block = source.split('{% block header_nav_collapse_left %}', 1)[1].split('{% endblock', 1)[0]
    assert 'miningtax/_nav.html' in nav_block
    assert '{% block header_nav_brand %}' in source
    content = source.split('{% block content %}', 1)[1]
    assert 'miningtax/_nav.html' not in content


def test_nav_renders_list_items_only():
    source = TEMPLATES.joinpath('_nav.html').read_text(encoding='utf-8')
    source = source.split('{% endcomment %}', 1)[1]  # markup only, not the explanation
    assert '<li class="nav-item">' in source
    assert '<ul' not in source and 'btn' not in source


def test_settings_page_is_centered():
    source = TEMPLATES.joinpath('settings.html').read_text(encoding='utf-8')
    assert '<div class="container mt-4">' in source
    assert 'container-fluid' not in source.split('{% block content %}', 1)[1].split('\n', 3)[2]
