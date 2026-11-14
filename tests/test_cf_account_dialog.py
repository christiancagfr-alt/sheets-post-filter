import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from cf_account_dialog import CloudflareAccountsDialog


class CloudflareDialogBindingTests(unittest.TestCase):
    def test_new_promo_site_updates_active_editor_and_persists_menu(self):
        dialog=object.__new__(CloudflareAccountsDialog)
        menu={'id':'filter-example','name':'示例筛选配置','template':'filter','settings':{}}
        app=SimpleNamespace(_menus=[menu],_active_menu_id=menu['id'],cfg=SimpleNamespace(ui_menus=[]),
            _store_current_menu_settings=Mock(),_save=Mock(),
            var_cf_url=Mock(),var_cf_secret=Mock())
        dialog.app=app;dialog.selected=lambda:{'id':'example-account-profile'};dialog.status=Mock()
        profile={'id':'example-account-profile'}
        site={'host':'example-gallery.pages.dev','publish_url':'https://example-gallery.pages.dev/api/publish-cache',
              'secret':'example-secret-value','kind':'promo'}
        with patch('cf_accounts.require_menu'),patch('cf_account_dialog.site_for_menu',return_value=(profile,site)):
            dialog._bind_deployed_site(menu['id'],site['host'])
        app._store_current_menu_settings.assert_called_once()
        self.assertEqual(menu['settings']['cf_profile_id'],profile['id'])
        self.assertEqual(menu['settings']['cf_publish_url'],site['publish_url'])
        self.assertEqual(menu['settings']['cf_publish_secret'],site['secret'])
        app.var_cf_url.set.assert_called_once_with(site['publish_url'])
        app.var_cf_secret.set.assert_called_once_with(site['secret'])
        app._save.assert_called_once_with(quiet=True)
