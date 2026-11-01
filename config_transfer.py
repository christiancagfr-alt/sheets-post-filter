"""Password-encrypted, portable configuration transfer. Never deploys resources."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = 'data-summary-transfer/v1'
AAD = MAGIC.encode('ascii')
MAX_SIZE = 32 * 1024 * 1024


def _key(password, salt):
    if not isinstance(password, str) or len(password) < 10:
        raise ValueError('迁移密码至少需要 10 个字符，请妥善保存，遗忘后无法恢复备份。')
    return Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode('utf-8'))


def _atomic_bytes(path, data):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.private-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def export_bundle(path, password, config, cloudflare, *, include_credentials=True):
    from fetch_posts import Config, discover_credential_files, normalize_credential_paths, _credential_candidates
    cfg = Config()
    for key, value in config.items():
        if hasattr(cfg, key):
            setattr(cfg, key, value)
    credentials = []
    if include_credentials:
        menus = list(cfg.ui_menus or []) + list(cfg.ui_archived_menus or [])
        paths = normalize_credential_paths(discover_credential_files(cfg, menus) + _credential_candidates(cfg, menus))
        for name in paths:
            file = Path(name)
            if file.stat().st_size > 1024 * 1024:
                raise ValueError('服务账号文件过大，未导出。')
            data = json.loads(file.read_text(encoding='utf-8-sig'))
            if not isinstance(data, dict) or data.get('type') != 'service_account' or not data.get('private_key') or not data.get('client_email'):
                raise ValueError('服务账号文件格式不正确，未导出。')
            credentials.append({'path': name, 'data': data})
    def canonical_paths(value, key=''):
        if isinstance(value, dict):
            return {k: canonical_paths(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [canonical_paths(v, key) for v in value]
        if key in ('credentials_file', 'credentials_files') and isinstance(value, str) and value:
            return str(Path(value).expanduser().resolve())
        return value
    payload = {'config': canonical_paths(config), 'cloudflare': copy.deepcopy(cloudflare), 'credentials': credentials}
    raw = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(raw) > MAX_SIZE // 2:
        raise ValueError('配置文件过大，未导出。')
    salt, nonce = secrets.token_bytes(16), secrets.token_bytes(12)
    encrypted = AESGCM(_key(password, salt)).encrypt(nonce, raw, AAD)
    encode = lambda value: base64.b64encode(value).decode('ascii')
    bundle = {'format': MAGIC, 'salt': encode(salt), 'nonce': encode(nonce), 'data': encode(encrypted)}
    _atomic_bytes(path, json.dumps(bundle, separators=(',', ':')).encode('utf-8'))
    return summarize_bundle(payload)


def read_bundle(path, password):
    file = Path(path)
    if file.stat().st_size > MAX_SIZE:
        raise ValueError('迁移文件过大，未读取。')
    try:
        outer = json.loads(file.read_bytes())
        if not isinstance(outer, dict) or outer.get('format') != MAGIC:
            raise ValueError()
        salt, nonce, encrypted = [base64.b64decode(outer[key], validate=True) for key in ('salt', 'nonce', 'data')]
        if len(salt) != 16 or len(nonce) != 12:
            raise ValueError()
        raw = AESGCM(_key(password, salt)).decrypt(nonce, encrypted, AAD)
        payload = json.loads(raw)
        _validate(payload)
        return payload
    except (InvalidTag, ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise ValueError('迁移密码错误，或文件损坏 / 格式不受支持；本机配置未改动。') from None


def _validate(payload):
    if not isinstance(payload, dict) or not isinstance(payload.get('config'), dict) or not isinstance(payload.get('cloudflare'), dict):
        raise ValueError('配置格式不正确')
    cfg = payload['config']
    ids = set()
    for key in ('ui_menus', 'ui_archived_menus'):
        rows = cfg.get(key) or []
        if not isinstance(rows, list) or len(rows) > 500:
            raise ValueError('菜单配置格式不正确')
        for menu in rows:
            if not isinstance(menu, dict) or not isinstance(menu.get('id'), str) or not menu['id'] or menu['id'] in ids:
                raise ValueError('菜单编号重复或格式不正确')
            if not isinstance(menu.get('settings'), dict) or not isinstance(menu.get('template'), str) or not isinstance(menu.get('name'), str):
                raise ValueError('菜单格式不正确')
            ids.add(menu['id'])
    for key in ('profiles', 'sites'):
        rows = payload['cloudflare'].get(key) or []
        if not isinstance(rows, list) or len(rows) > 500 or any(not isinstance(row, dict) for row in rows):
            raise ValueError('账号配置格式不正确')
    credentials = payload.get('credentials')
    if not isinstance(credentials, list) or len(credentials) > 200:
        raise ValueError('服务账号配置格式不正确')
    for item in credentials:
        if not isinstance(item, dict) or not isinstance(item.get('path'), str) or not isinstance(item.get('data'), dict):
            raise ValueError('服务账号配置格式不正确')
        data = item['data']
        if data.get('type') != 'service_account' or not data.get('private_key') or not data.get('client_email'):
            raise ValueError('服务账号格式不正确')


def summarize_bundle(payload):
    from promo_deploy import known_gallery_sites
    return {'menus': len(payload['config'].get('ui_menus') or []),
            'sites': len(known_gallery_sites(payload['cloudflare'])),
            'credentials': len(payload.get('credentials') or [])}


def _merge_rows(current, incoming, key):
    result = copy.deepcopy(current or [])
    for row in incoming or []:
        identity = row.get(key)
        if not identity:
            raise ValueError('配置编号缺失，未导入。')
        index = next((i for i, old in enumerate(result) if old.get(key) == identity), None)
        if index is None:
            result.append(copy.deepcopy(row))
        else:
            result[index] = copy.deepcopy(row)
    return result


def import_bundle(payload, directory):
    """Merge matching stable IDs, pause incoming timers, atomically roll back on failure."""
    from dataclasses import asdict
    from fetch_posts import Config
    from local_secrets import atomic_json_write, read_private_json
    from promo_deploy import known_gallery_sites
    _validate(payload)
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    config_path, cf_path = directory / 'config.json', directory / 'cloudflare.json'
    current = read_private_json(config_path, migrate=False) if config_path.exists() else asdict(Config())
    cf = read_private_json(cf_path, migrate=False) if cf_path.exists() else {}
    imported = copy.deepcopy(payload['config'])
    mapping = {}
    files = {}
    for item in payload['credentials']:
        data = json.dumps(item['data'], ensure_ascii=False, sort_keys=True).encode('utf-8')
        name = 'credentials-' + hashlib.sha256(data).hexdigest()[:24] + '.json'
        target = directory / 'migrated-credentials' / name
        if target.is_symlink() or target.parent.is_symlink():
            raise ValueError('服务账号目录不能是符号链接。')
        mapping[item['path']] = str(target)
        files[target] = data

    def remap(value, key=''):
        if isinstance(value, dict):
            return {k: remap(v, k) for k, v in value.items()}
        if isinstance(value, list):
            return [remap(v, key) for v in value]
        if key == 'credentials_file':
            return mapping.get(value, '') if isinstance(value, str) else ''
        if key == 'credentials_files':
            return mapping.get(value, '') if isinstance(value, str) else ''
        if key.endswith('schedule_enabled'):
            return False
        return value
    imported = remap(imported)
    # Keep this machine's usable accounts when export omitted service-account files.
    incoming_files = [p for p in imported.get('credentials_files', []) if p]
    imported['credentials_files'] = list(dict.fromkeys(list(current.get('credentials_files') or []) + incoming_files))
    imported['credentials_file'] = imported.get('credentials_file') or current.get('credentials_file', '')
    result = {**current, **{k: v for k, v in imported.items() if hasattr(Config(), k)}}
    for field in ('ui_menus', 'ui_archived_menus'):
        result[field] = _merge_rows(current.get(field), imported.get(field), 'id')
    # An imported active menu must not also survive in the archive.
    active_ids = {m['id'] for m in result['ui_menus']}
    result['ui_archived_menus'] = [m for m in result['ui_archived_menus'] if m['id'] not in active_ids]
    incoming_cf = copy.deepcopy(payload['cloudflare'])
    sites = _merge_rows(known_gallery_sites(cf), known_gallery_sites(incoming_cf), 'host')
    for new in known_gallery_sites(incoming_cf):
        old = next((s for s in known_gallery_sites(cf) if s['host'] == new['host']), None)
        if old and any(old.get(key) != new.get(key) for key in ('account_id', 'project', 'bucket', 'kind')):
            raise ValueError('同一域名对应不同账号或部署资源，未导入。请核对备份来源。')
    final_cf = {**cf, **incoming_cf, 'sites': sites,
                'profiles': _merge_rows(cf.get('profiles'), incoming_cf.get('profiles'), 'id')}
    originals = {p: p.read_bytes() if p.exists() else None for p in (config_path, cf_path, *files)}
    backup = directory / 'private-backup' / ('before-import-' + secrets.token_hex(8) + '.json')
    backup.parent.mkdir(parents=True, exist_ok=True)
    # The backup stays private locally, with existing DPAPI ciphertext intact.
    atomic_json_write(backup, {'secret': json.dumps({str(p.relative_to(directory)): base64.b64encode(data).decode('ascii')
                                                  for p, data in originals.items() if data is not None})})
    touched = []
    try:
        for path, data in files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.read_bytes() == data:
                continue
            touched.append(path)
            _atomic_bytes(path, data)
        for path, value in ((cf_path, final_cf), (config_path, result)):
            touched.append(path)
            atomic_json_write(path, value)
    except Exception:
        for path in reversed(touched):
            if originals[path] is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_bytes(path, originals[path])
        raise
    return {**summarize_bundle(payload), 'backup': str(backup)}
