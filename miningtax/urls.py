from django.urls import path
from . import views
from . import pdf_views

app_name = 'miningtax'

urlpatterns = [
    # ─── Hauptseiten ───────────────────────────────────────────────────────────
    path('', views.dashboard, name='dashboard'),
    path('sync/', views.sync_now, name='sync_now'),
    path('alliance/', views.alliance_overview, name='alliance_overview'),

    # Als bezahlt markieren (POST mit corp_id)
    path('alliance/paid/<int:corp_id>/', views.mark_paid, name='mark_paid'),

    # ─── Settings ─────────────────────────────────────────────────────────────
    path('settings/', views.settings_view, name='settings'),
    path('settings/taxrate/<int:pk>/save/', views.settings_save_taxrate, name='settings_save_taxrate'),
    path('settings/rental/add/', views.settings_add_rental, name='settings_add_rental'),
    path('settings/rental/<int:pk>/delete/', views.settings_delete_rental, name='settings_delete_rental'),
    path('settings/moon/add/', views.settings_add_moon, name='settings_add_moon'),
    path('settings/moon/<int:pk>/edit/', views.settings_edit_moon, name='settings_edit_moon'),
    path('settings/moon/<int:pk>/delete/', views.settings_delete_moon, name='settings_delete_moon'),

    # ─── PDF Downloads ─────────────────────────────────────────────────────────
    path('pdf/corp/<int:corp_id>/', pdf_views.download_corp_pdf, name='download_corp_pdf'),
    path('pdf/all/', pdf_views.download_all_corps_zip, name='download_all_corps_zip'),
]
