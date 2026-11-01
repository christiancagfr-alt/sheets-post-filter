import copy
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from config_transfer import export_bundle, read_bundle, import_bundle
from fetch_posts import Config
from local_secrets import atomic_json_write, read_private_json

PASSWORD='example-migration-password'


def configuration():
    value=asdict(Config())
    value.update(ui_active_menu='example-menu',ui_menus=[{
        'id':'example-menu','name':'示例','template':'worksheet_merge',
        'settings':{'wm_source_url':'https://example.com/source','wm_schedule_enabled':True,
                    'cf_profile_id':'example-owner','wm_gallery_host':'gallery.example.com',
                    'credentials_file':'','credentials_files':[]}}])
    return value


def cloudflare():
    return {'profiles':[{'id':'example-owner','account_id':'example-owner','api_token':'example-token',
                         'allowed_menu_ids':['example-menu']}],
            'sites':[{'host':'gallery.example.com','account_id':'example-owner','project':'example-project',
                      'bucket':'example-bucket','kind':'summary','secret':'example-publish-key','access_password':'example-access'}]}


class TransferTests(unittest.TestCase):
    def test_roundtrip_has_no_plaintext_or_machine_encryption(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'example.dsconfig'
            export_bundle(path,PASSWORD,configuration(),cloudflare(),include_credentials=False)
            raw=path.read_text()
            for private in ('example-token','example-publish-key','example-access','gallery.example.com','example-menu','win-dpapi'):
                self.assertNotIn(private,raw)
            result=read_bundle(path,PASSWORD)
            self.assertEqual(result['cloudflare'],cloudflare())
            self.assertEqual(result['config'],configuration())

    def test_wrong_password_and_tampering_do_not_import(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'example.dsconfig'
            export_bundle(path,PASSWORD,configuration(),cloudflare(),include_credentials=False)
            with self.assertRaises(ValueError):read_bundle(path,'example-wrong-password')
            value=json.loads(path.read_text());value['data']='AAAA'+value['data'][4:]
            path.write_text(json.dumps(value))
            with self.assertRaises(ValueError):read_bundle(path,PASSWORD)

    def test_import_preserves_ids_pauses_imported_timers_and_keeps_local_menus(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            old=configuration();old['ui_menus'].append({'id':'local','name':'示例本机','template':'video','settings':{}})
            atomic_json_write(directory/'config.json',old)
            atomic_json_write(directory/'cloudflare.json',cloudflare())
            payload={'config':configuration(),'cloudflare':cloudflare(),'credentials':[]}
            original=copy.deepcopy(payload)
            result=import_bundle(payload,directory)
            current=read_private_json(directory/'config.json')
            self.assertEqual([m['id'] for m in current['ui_menus']],['example-menu','local'])
            self.assertFalse(current['ui_menus'][0]['settings']['wm_schedule_enabled'])
            self.assertEqual(current['ui_active_menu'],'example-menu')
            self.assertEqual(payload,original)
            self.assertTrue(Path(result['backup']).exists())
            self.assertEqual(read_private_json(directory/'cloudflare.json')['sites'][0]['secret'],'example-publish-key')

    def test_credentials_are_rebased_not_extracted_to_untrusted_paths(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            cfg=configuration();cfg['credentials_file']='../../outside.json';cfg['credentials_files']=['../../outside.json']
            cfg['ui_menus'][0]['settings'].update(credentials_file='../../outside.json',credentials_files=['../../outside.json'])
            payload={'config':cfg,'cloudflare':cloudflare(),'credentials':[{'path':'../../outside.json','data':{
                'type':'service_account','client_email':'example@example.invalid','private_key':'example-private-key'}}]}
            import_bundle(payload,directory)
            current=read_private_json(directory/'config.json')
            target=Path(current['credentials_file'])
            self.assertEqual(target.parent,(directory/'migrated-credentials').resolve())
            self.assertEqual(current['ui_menus'][0]['settings']['credentials_file'],str(target))
            self.assertEqual(json.loads(target.read_text())['private_key'],'example-private-key')

    def test_conflicting_site_aborts_without_writing(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            atomic_json_write(directory/'cloudflare.json',cloudflare())
            original=(directory/'cloudflare.json').read_bytes()
            other=cloudflare();other['sites'][0]['project']='different-example'
            with self.assertRaises(ValueError):import_bundle({'config':configuration(),'cloudflare':other,'credentials':[]},directory)
            self.assertEqual((directory/'cloudflare.json').read_bytes(),original)
            self.assertFalse((directory/'config.json').exists())

    def test_failed_config_write_restores_original_cf_and_config(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            atomic_json_write(directory/'config.json',configuration());atomic_json_write(directory/'cloudflare.json',cloudflare())
            before={name:(directory/name).read_bytes() for name in ('config.json','cloudflare.json')}
            def write(path,value,**kwargs):
                if Path(path).name=='config.json':raise OSError('example failure')
                return atomic_json_write(path,value,**kwargs)
            with patch('local_secrets.atomic_json_write',side_effect=write):
                with self.assertRaises(OSError):import_bundle({'config':configuration(),'cloudflare':cloudflare(),'credentials':[]},directory)
            self.assertEqual({name:(directory/name).read_bytes() for name in before},before)

    def test_duplicate_menu_ids_rejected_before_write(self):
        cfg=configuration();cfg['ui_menus']*=2
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):import_bundle({'config':cfg,'cloudflare':{},'credentials':[]},folder)
            self.assertEqual(list(Path(folder).iterdir()),[])

    def test_weak_password_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'example.dsconfig'
            with self.assertRaises(ValueError):export_bundle(path,'short',configuration(),{},include_credentials=False)
            self.assertFalse(path.exists())

    def test_exported_service_accounts_migrate_to_another_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            original=directory/'credentials-example.json'
            original.write_text(json.dumps({'type':'service_account','client_email':'example@example.invalid','private_key':'example-key'}))
            cfg=configuration()
            cfg['credentials_file']=str(directory/'.'/original.name)
            cfg['credentials_files']=[str(original)]
            backup=directory/'example.dsconfig'
            export_bundle(backup,PASSWORD,cfg,cloudflare())
            payload=read_bundle(backup,PASSWORD)
            destination=directory/'new-machine'
            import_bundle(payload,destination)
            restored=read_private_json(destination/'config.json')
            self.assertTrue(Path(restored['credentials_file']).exists())
            self.assertNotEqual(Path(restored['credentials_file']),original)
            self.assertEqual(len(restored['credentials_files']),1)


if __name__=='__main__':unittest.main()
