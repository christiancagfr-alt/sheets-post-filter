import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch
from promo_deploy import deploy_gallery, CloudflareError


class OptionalDomainTests(unittest.TestCase):
    def test_default_pages_address_uses_assigned_host_without_dns(self):
        for kind in ('summary', 'promo'):
            with self.subTest(kind=kind), ExitStack() as stack:
                api = Mock()
                api.result.side_effect = lambda value: value
                api.get.side_effect = [CloudflareError('404 not found'),
                    {'name': 'example-new', 'subdomain': 'example-assigned.pages.dev'}]
                stack.enter_context(patch('promo_deploy.CloudflareAPI', return_value=api))
                stack.enter_context(patch('promo_deploy.load_cf_state', return_value={}))
                stack.enter_context(patch('gallery_management.find_project', return_value=None))
                mocks = {}
                for name in ('_ensure_bucket', '_ensure_project', '_configure_project',
                             'collect_static_files', '_upload_assets', '_create_deployment',
                             'wait_for_deployment', '_attach_domain', 'save_cf_state'):
                    mocks[name] = stack.enter_context(patch('promo_deploy.' + name))
                mocks['collect_static_files'].return_value = ['example']
                mocks['_upload_assets'].return_value = {}
                mocks['_create_deployment'].return_value = {'id': 'example-deploy'}
                result = deploy_gallery(token='example-token', account_id='example-account',
                    create_if_missing=True, project_name='example-new', site_kind=kind)
                self.assertEqual(result['host'], 'example-assigned.pages.dev')
                self.assertEqual(result['site_url'], 'https://example-assigned.pages.dev')
                self.assertEqual(result['pages_url'], result['site_url'])
                self.assertEqual(result['project'], 'example-new')
                self.assertEqual(result['bucket'], 'example-new-json')
                self.assertTrue(result['publish_url'].startswith(result['site_url'] + '/api/'))
                mocks['_attach_domain'].assert_not_called()
                self.assertEqual(mocks['_configure_project'].call_args.args[5],
                                 'https://example-assigned.pages.dev/cdn')
                saved = mocks['save_cf_state'].call_args.args[0]
                self.assertEqual(saved['sites'][0]['host'], result['host'])

    def test_blank_address_cannot_accidentally_create_during_update(self):
        with patch('promo_deploy.CloudflareAPI') as factory:
            with self.assertRaises(CloudflareError):
                deploy_gallery(force_update=True)
            factory.assert_not_called()

    def test_invalid_project_name_fails_before_network(self):
        with patch('promo_deploy.CloudflareAPI') as factory:
            with self.assertRaises(CloudflareError):
                deploy_gallery(create_if_missing=True, project_name='invalid/name')
            factory.assert_not_called()
