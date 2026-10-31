from __future__ import annotations

import sys
import unittest
import json
from unittest.mock import patch
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from promo_deploy import (  # noqa: E402
    AUTH_HELP,
    CloudflareError,
    CloudflareAPI,
    TOKEN_CREATE_URL,
    TOKEN_PERMISSIONS,
    generate_publish_secret,
    known_gallery_sites,
    is_auth_failure,
    is_missing_resource,
    looks_like_global_api_key,
    normalize_host,
    pages_env_vars,
    pages_file_hash,
    resolve_gallery_resources,
    rewrite_gallery_html,
    slug_from_host,
    login_with_token,
    _attach_domain,
)


class PromoDeployHelperTests(unittest.TestCase):
    def test_direct_upload_sends_routes_as_deployment_form_file(self):
        from promo_deploy import _create_deployment
        routes=b'{"version":1,"include":["/api/*"],"exclude":[]}'
        headers=b'/*\n  X-Robots-Tag: noindex\n'
        with patch('promo_deploy.CloudflareAPI') as factory:
            api=factory.return_value
            api.result.return_value={'id':'fixture'}
            _create_deployment(api,'account','existing',{'/index.html':'hash'},b'export default {}',lambda _:None,routes=routes,headers=headers)
        fields=dict(api.post.call_args.kwargs['form'])
        self.assertEqual(fields['_routes.json'][1],routes)
        self.assertEqual(fields['_headers'][1],headers)
        self.assertEqual(fields['branch'],'production')

    def test_pending_domain_uses_pages_address_instead_of_reporting_custom_domain_ready(self):
        from unittest.mock import Mock
        api=Mock();api.result.side_effect=lambda data:data
        api.get.side_effect=[[{'id':'zone'}],[],{'name':'gallery.example.com','status':'pending'}]
        api.post.side_effect=[{'success':True},CloudflareError('A record with the same name exists')]
        result=_attach_domain(api,'owner','my-project','gallery.example.com',lambda _:None)
        self.assertEqual(result,'https://my-project.pages.dev')
        api.patch.assert_not_called()

    def test_r2_not_enabled_is_not_misreported_as_token_permission(self):
        body=json.dumps({'success':False,'errors':[{'code':10042,'message':'Please enable R2 through the Cloudflare Dashboard.'}]})
        error=CloudflareAPI._response_error(403,body)
        self.assertEqual(error.status,403)
        self.assertEqual(error.code,10042)
        self.assertIn('尚未开通 R2',str(error))
        self.assertNotIn('重新点',str(error))
        self.assertFalse(is_auth_failure(error))
        api=CloudflareAPI('test-token')
        with patch.object(api,'get',side_effect=[{'success':True},error]):
            with self.assertRaises(CloudflareError) as raised:api.verify_deploy_permissions('account')
        self.assertEqual(raised.exception.code,10042)
        self.assertIn('尚未开通',str(raised.exception))

    def test_real_r2_permission_denial_preserves_cloudflare_code_and_account_context(self):
        error=CloudflareAPI._response_error(403,json.dumps({'errors':[{'code':10000,'message':'Authentication error'}]}))
        api=CloudflareAPI('test-token')
        with patch.object(api,'get',side_effect=[{'success':True},error]):
            with self.assertRaises(CloudflareError) as raised:api.verify_deploy_permissions('account')
        self.assertIn('账号资源范围',str(raised.exception))
        self.assertIn('10000',str(raised.exception))
        self.assertEqual(raised.exception.status,403)

    def test_account_login_does_not_require_r2_and_honors_selected_account(self):
        account_list=[{'id':'first','name':'第一'},{'id':'second','name':'第二'}]
        with patch('promo_deploy.CloudflareAPI') as api_class,patch('promo_deploy.save_cf_state'),patch('cf_accounts.register_accounts') as register:
            api=api_class.return_value;api.list_accounts.return_value=account_list
            state=login_with_token('test-token',account_id='second',allowed_menu_ids=['menu'])
            self.assertEqual(state['account_id'],'second')
            api.verify_deploy_permissions.assert_not_called()
            register.assert_called_once_with('test-token',account_list,allowed_menu_ids=['menu'])
            with self.assertRaisesRegex(CloudflareError,'资源范围'):login_with_token('test-token',account_id='not-accessible')

    def test_normalize_host_strips_scheme_and_path(self):
        self.assertEqual(normalize_host("https://Gallery.Example.com/foo"), "gallery.example.com")

    def test_slug_from_host_is_pages_safe(self):
        slug = slug_from_host("Gallery.Example.com")
        self.assertTrue(slug.startswith("gallery-"))
        self.assertLessEqual(len(slug), 58)
        self.assertRegex(slug, r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$")

    def test_rewrite_html_points_cdn_at_new_host(self):
        src = (
            'window.Q_GALLERY_DRIVE_CACHE_MANIFEST_URL = '
            '"https://cdn.old-example.net/promo/manifest.json";\n'
            'window.Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL = '
            '"https://drive.google.com/uc?export=download&id=abc";'
        )
        out = rewrite_gallery_html(src, "promo.my-site.com")
        self.assertIn("https://promo.my-site.com/cdn/promo/manifest.json", out)
        self.assertNotIn("cdn.old-example.net", out)
        self.assertIn('Q_GALLERY_DRIVE_FALLBACK_MANIFEST_URL = ""', out)

    def test_secret_is_long_and_unique(self):
        first = generate_publish_secret()
        second = generate_publish_secret()
        self.assertGreaterEqual(len(first), 32)
        self.assertNotEqual(first, second)

    def test_pages_hash_is_32_hex(self):
        digest = pages_file_hash(b"<html>ok</html>", "index.html")
        self.assertEqual(len(digest), 32)
        self.assertRegex(digest, r"^[0-9a-f]{32}$")

    def test_token_create_url_uses_cloudflare_permission_keys(self):
        keys = {item["key"] for item in TOKEN_PERMISSIONS}
        self.assertEqual(keys, {"page", "workers_r2", "workers_scripts", "dns", "account_settings"})
        self.assertNotIn("pages", keys)
        self.assertNotIn("workers_r2_storage", keys)
        self.assertIn("permissionGroupKeys=", TOKEN_CREATE_URL)
        self.assertIn("accountId=", TOKEN_CREATE_URL)
        self.assertIn("zoneId=all", TOKEN_CREATE_URL)
        self.assertIn("page", TOKEN_CREATE_URL)
        self.assertIn("workers_r2", TOKEN_CREATE_URL)
        self.assertNotIn("workers_r2_storage", TOKEN_CREATE_URL)

    def test_auth_error_tells_user_to_recreate_token(self):
        body = '{"success":false,"errors":[{"code":10000,"message":"Authentication error"}]}'
        text = CloudflareAPI._format_error(403, body)
        self.assertIn("拒绝了这个令牌", text)
        self.assertIn("10000", text)
        self.assertIn("登录 Cloudflare", text)
        self.assertTrue(is_auth_failure(Exception(text)))
        self.assertFalse(is_missing_resource(Exception(text)))
        self.assertTrue(is_missing_resource(Exception("Cloudflare HTTP 404：not found")))
        self.assertTrue(looks_like_global_api_key("a" * 37))
        self.assertFalse(looks_like_global_api_key("cfut_not_a_global_key_example_token"))
        self.assertIn("登录 Cloudflare", AUTH_HELP)
        self.assertIn("Pages 编辑", AUTH_HELP)

    def test_pages_env_vars_set_or_clear_access_password(self):
        with_pw = pages_env_vars("pub-secret", "https://example.com/cdn", "site-pass")
        self.assertEqual(with_pw["ACCESS_PASSWORD"]["value"], "site-pass")
        self.assertEqual(with_pw["ACCESS_PASSWORD"]["type"], "secret_text")
        cleared = pages_env_vars("pub-secret", "https://example.com/cdn", "")
        self.assertIsNone(cleared["ACCESS_PASSWORD"])
        self.assertEqual(cleared["CACHE_PUBLISH_SECRET"]["value"], "pub-secret")

    def test_new_domain_gets_its_own_project_and_secret(self):
        state = {
            "host": "sucai.example.com",
            "project": "gallery-sucai-example-com",
            "bucket": "gallery-json-sucai-example-com",
            "secret": "old-site-secret-value",
        }
        first = resolve_gallery_resources("sucai.example.com", state)
        self.assertTrue(first["reused"])
        self.assertEqual(first["project"], "gallery-sucai-example-com")
        self.assertEqual(first["secret"], "old-site-secret-value")
        second = resolve_gallery_resources("gallery.example.com", state)
        self.assertFalse(second["reused"])
        self.assertEqual(second["project"], "gallery-gallery-example-com")
        self.assertNotEqual(second["bucket"], state["bucket"])
        self.assertNotEqual(second["secret"], state["secret"])
        self.assertNotIn(second["project"], {"q-gallery", "q-gallery-promo"})
        self.assertNotIn(second["bucket"], {"q-gallery-json-cache"})

    def test_promo_hostname_does_not_reuse_production_project_name(self):
        resources = resolve_gallery_resources("promo.example.com", {})
        self.assertEqual(resources["project"], "gallery-promo-example-com")
        self.assertNotEqual(resources["project"], "q-gallery-promo")
        self.assertNotEqual(resources["bucket"], "q-gallery-json-cache")

    def test_known_sites_keep_previous_host_secrets(self):
        state = {
            "host": "b.example.com",
            "project": "gallery-b-example-com",
            "bucket": "gallery-json-b-example-com",
            "secret": "secret-b",
            "sites": [
                {
                    "host": "a.example.com",
                    "project": "gallery-a-example-com",
                    "bucket": "gallery-json-a-example-com",
                    "secret": "secret-a",
                    "publish_url": "https://a.example.com/api/publish-cache",
                }
            ],
        }
        hosts = {item["host"]: item["secret"] for item in known_gallery_sites(state)}
        self.assertEqual(hosts["a.example.com"], "secret-a")
        self.assertEqual(hosts["b.example.com"], "secret-b")
        restored = resolve_gallery_resources("a.example.com", state)
        self.assertTrue(restored["reused"])
        self.assertEqual(restored["secret"], "secret-a")


if __name__ == "__main__":
    unittest.main()
