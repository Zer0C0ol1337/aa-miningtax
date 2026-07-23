from django.urls import path
from . import views
from . import pdf_views
from . import api_views
from . import csv_views

app_name = 'miningtax'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('sync/', views.sync_now, name='sync_now'),
    path('alliance/', views.alliance_overview, name='alliance_overview'),
    path('alliance/paid/<int:corp_id>/', views.mark_paid, name='mark_paid'),
    path('alliance/unpaid/<int:corp_id>/', views.mark_unpaid, name='mark_unpaid'),
    path('alliance/check-payments/', views.check_payments_now, name='check_payments_now'),
    path('alliance/pilot/<int:character_id>/', views.pilot_detail, name='pilot_detail'),

    path('settings/', views.settings_view, name='settings'),
    path('settings/taxrate/<int:pk>/save/', views.settings_save_taxrate, name='settings_save_taxrate'),
    path('settings/taxrate/add/', views.settings_add_taxrate, name='settings_add_taxrate'),
    path('settings/rental/add/', views.settings_add_rental, name='settings_add_rental'),
    path('settings/rental/<int:pk>/delete/', views.settings_delete_rental, name='settings_delete_rental'),
    path('settings/moon/add/', views.settings_add_moon, name='settings_add_moon'),
    path('settings/moon/<int:pk>/edit/', views.settings_edit_moon, name='settings_edit_moon'),
    path('settings/moon/<int:pk>/delete/', views.settings_delete_moon, name='settings_delete_moon'),
    path('settings/treasury/add/', views.settings_add_treasury, name='settings_add_treasury'),
    path('settings/treasury/<int:pk>/delete/', views.settings_delete_treasury, name='settings_delete_treasury'),
    path('settings/register-corp/', views.settings_register_corp, name='settings_register_corp'),
    path('settings/register-alliance-corps/', views.settings_register_alliance_corps, name='settings_register_alliance_corps'),
    path('settings/sov-filter/add/', views.settings_add_sov_filter, name='settings_add_sov_filter'),
    path('settings/sov-filter/<int:pk>/delete/', views.settings_delete_sov_filter, name='settings_delete_sov_filter'),
    path('settings/sov-filter/sync-now/', views.settings_sync_sov_now, name='settings_sync_sov_now'),
    path('settings/janice/save/', views.settings_save_janice, name='settings_save_janice'),
    path('settings/ore-categories/sync/', views.settings_sync_ore_categories, name='settings_sync_ore_categories'),
    path('settings/repair-names/', views.settings_repair_names, name='settings_repair_names'),
    path('settings/update-prices/', views.settings_update_prices, name='settings_update_prices'),
    path('settings/scope/add/', views.settings_add_scope, name='settings_add_scope'),
    path('settings/scope/<int:pk>/delete/', views.settings_delete_scope, name='settings_delete_scope'),
    path('settings/exemption/add/', views.settings_add_exemption, name='settings_add_exemption'),
    path('settings/exemption/<int:pk>/delete/', views.settings_delete_exemption, name='settings_delete_exemption'),
    path('settings/exemption/<int:pk>/toggle/', views.settings_toggle_exemption, name='settings_toggle_exemption'),

    path('csv/my-ledger/', csv_views.export_my_ledger, name='export_my_ledger'),
    path('csv/pilot/<int:character_id>/', csv_views.export_pilot_ledger, name='export_pilot_ledger'),
    path('csv/alliance/', csv_views.export_alliance_billing, name='export_alliance_billing'),

    path('pdf/corp/<int:corp_id>/', pdf_views.download_corp_pdf, name='download_corp_pdf'),
    path('pdf/all/', pdf_views.download_all_corps_zip, name='download_all_corps_zip'),

    # JSON endpoint feeding the dependent moon dropdown in the Settings UI
    path('api/moons/', api_views.api_moons_for_system, name='api_moons_for_system'),
    path('api/structures/', api_views.api_structures_for_corp, name='api_structures_for_corp'),
]