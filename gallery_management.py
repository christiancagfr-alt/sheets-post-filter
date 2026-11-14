"""Connect to existing galleries and change passwords without recreating resources."""
from pathlib import Path
import re
import time

from promo_deploy import (CloudflareError, known_gallery_sites, load_cf_state, normalize_host,
                         save_cf_state, _upload_assets, _create_deployment,
                         collect_static_files, rewrite_gallery_html, RESOURCE_DIR, wait_for_deployment)


def find_project(api, account_id, host, *, project_hint=''):
    host = normalize_host(host)
    if project_hint:
        project = api.result(api.get(f'/accounts/{account_id}/pages/projects/{project_hint}'))
        if not owns_host(project, host):
            raise CloudflareError('已记录项目未绑定这个域名，已停止；请核对站点配置。')
        return project
    for page in range(1, 201):
        response = api.get(f'/accounts/{account_id}/pages/projects?page={page}')
        rows = api.result(response) or []
        matches = [row for row in rows if owns_host(row, host)]
        if len(matches) > 1:
            raise CloudflareError('此域名匹配多个项目，请导入原配置明确指定项目。')
        if matches:
            return api.result(api.get(f"/accounts/{account_id}/pages/projects/{matches[0]['name']}"))
        # Let Cloudflare choose its supported page size. Never infer the end
        # from a requested size: the service may cap it independently.
        info = response.get('result_info') or {}
        total_pages = info.get('total_pages')
        if not rows or (total_pages is not None and page >= int(total_pages)):
            return None
    raise CloudflareError('账号项目过多，未能完整检查；请导入原站点配置。')


def owns_host(project, host):
    return host in {str(x).lower() for x in list(project.get('domains') or []) + [project.get('subdomain') or '']}


def validate_project(project, site):
    if not project or project.get('name') != site['project'] or not owns_host(project, site['host']):
        raise CloudflareError('站点项目或域名不匹配，未修改部署。')
    production = (project.get('deployment_configs') or {}).get('production') or {}
    bucket = (production.get('r2_buckets') or {}).get('GALLERY_CACHE') or {}
    if bucket.get('name') != site['bucket']:
        raise CloudflareError('线上 R2 绑定与原配置不一致，未覆盖。请导入最新站点配置。')
    return production


def connect_existing(api, account_id, host, secret, *, kind='summary', profile_id='', project_hint='', log=lambda _: None):
    project = find_project(api, account_id, host, project_hint=project_hint)
    if not project:
        raise CloudflareError('此账号没有绑定该域名的项目；连接现有站点不会创建任何资源。')
    if kind not in ('summary','promo'):
        raise CloudflareError('不支持的网站类型')
    subdomain = str(project.get('subdomain') or '')
    if not re.fullmatch(r'[a-z0-9-]+\.pages\.dev', subdomain):
        raise CloudflareError('无法核验项目的固定 Pages 地址。')
    if not secret:
        raise CloudflareError('请导入原电脑配置，或填写原站点发布密钥；不会自动生成新密钥覆盖。')
    from summary_publish import _post
    endpoint = '/api/summary-publish' if kind == 'summary' else '/api/publish-cache'
    expected = 'summary-gallery/v1' if kind == 'summary' else 'sheets-gallery-publish-cache'
    hello = _post('https://' + subdomain + endpoint, secret, {'action': 'hello'}, lambda: False)
    if hello.get('service') != expected:
        raise CloudflareError('现有网站与所选网站类型不匹配，未修改。')
    production = (project.get('deployment_configs') or {}).get('production') or {}
    bucket = (production.get('r2_buckets') or {}).get('GALLERY_CACHE', {}).get('name')
    if not bucket:
        raise CloudflareError('现有站点没有图库 R2 绑定，未修改。')
    state = load_cf_state()
    old = next((s for s in known_gallery_sites(state) if s['host'] == normalize_host(host)), {})
    site = {**old, 'host': normalize_host(host), 'account_id': account_id, 'profile_id': profile_id or account_id,
            'project': project['name'], 'bucket': bucket, 'kind': kind, 'secret': secret,
            'pages_url': 'https://' + subdomain, 'site_url': 'https://' + normalize_host(host),
            'publish_url': 'https://' + normalize_host(host) + endpoint}
    if old.get('account_id') and old['account_id'] != account_id:
        raise CloudflareError('此域名已记录在其他账号下，未覆盖。')
    save_cf_state({'sites': [s for s in known_gallery_sites(state) if s['host'] != site['host']] + [site]})
    log('已连接原站点，未部署网页、未创建资源、未更换密钥。日常直接点“发布图库”。')
    return {**site, 'ok': True, 'reused': True, 'deployed': False}


def redeploy_existing(api, account_id, site, log=lambda _: None):
    """Apply changed environment settings using the existing project's bindings."""
    project = find_project(api, account_id, site['host'], project_hint=site['project'])
    validate_project(project, site)
    folder = RESOURCE_DIR / ('summary-gallery' if site.get('kind') == 'summary' else 'promo-site')
    if not (folder / 'gallery-worker.js').exists():
        folder = Path(__file__).resolve().parent / folder.name
    if not (folder / 'gallery-worker.js').exists():
        raise CloudflareError('本机缺少对应的网页模板，原站点未重新部署。')
    manifest = _upload_assets(api, account_id, site['project'], collect_static_files(folder, site['host']), log)
    deployment = _create_deployment(api, account_id, site['project'], manifest,
        rewrite_gallery_html((folder / 'gallery-worker.js').read_text(encoding='utf-8'), site['host']).encode('utf-8'), log,
        routes=(folder / '_routes.json').read_bytes() if (folder / '_routes.json').exists() else None,
        headers=(folder / '_headers').read_bytes() if (folder / '_headers').exists() else None,
        production_branch=project.get('production_branch') or 'production')
    wait_for_deployment(api, account_id, site['project'], deployment, log)
    return deployment


def sync_publish_settings(settings, site, secret):
    """Only configurations bound to this site's URLs and owner receive a new key."""
    from urllib.parse import urlsplit
    hosts = {site['host'], urlsplit(site.get('pages_url') or '').hostname,
             urlsplit(site.get('site_url') or '').hostname}
    owner = settings.get('cf_profile_id') or ''
    if owner and owner not in {site.get('profile_id'), site.get('account_id')}:
        return False
    if urlsplit(settings.get('cf_publish_url') or '').hostname not in hosts - {None, ''}:
        return False
    settings['cf_publish_secret'] = secret
    return True


def _sync_saved_publish_settings(site, secret):
    from fetch_posts import SCRIPT_DIR, _CONFIG_WRITE_LOCK
    from local_secrets import read_private_json, atomic_json_write
    path = SCRIPT_DIR / 'config.json'
    if not path.exists(): return
    with _CONFIG_WRITE_LOCK:
        data = read_private_json(path, migrate=False)
        changed = sync_publish_settings(data,site,secret)
        for menu in (data.get('ui_menus') or []) + (data.get('ui_archived_menus') or []):
            changed = sync_publish_settings(menu.get('settings') or {},site,secret) or changed
        if changed: atomic_json_write(path,data)


def _save_pending(site, field, value):
    state = load_cf_state()
    pending = dict(state.get('pending_credentials') or {})
    # Values use existing encrypted field names; never print them to logs.
    entry = dict(pending.get(site['host']) or {})
    entry.update(account_id=site['account_id'], **{field:value})
    pending[site['host']] = entry
    save_cf_state({'pending_credentials':pending})


def _finish_credential(site, field, value):
    latest = load_cf_state()
    sites = known_gallery_sites(latest)
    for row in sites:
        if row['host'] == site['host'] and row.get('account_id') == site['account_id']:
            row[field] = value
    if field == 'secret': _sync_saved_publish_settings(site,value)
    pending = dict(latest.get('pending_credentials') or {})
    entry = dict(pending.get(site['host']) or {})
    entry.pop(field,None)
    if not any(key in entry for key in ('secret','access_password')): pending.pop(site['host'],None)
    else: pending[site['host']] = entry
    updates = {'sites':sites,'pending_credentials':pending}
    if latest.get('host') == site['host']:
        updates[field] = value
    save_cf_state(updates)


def _credential_site(api, account_id, host):
    host = normalize_host(host)
    site = next((s for s in known_gallery_sites(load_cf_state())
                 if s['host'] == host and s.get('account_id') == account_id),None)
    if not site or site.get('kind','promo') not in ('summary','promo'):
        raise CloudflareError('请先选择当前账号下已保存的图库或成品库站点。')
    project = find_project(api,account_id,host,project_hint=site['project'])
    validate_project(project,site)
    return site,project


def _apply_credential(api, account_id, host, field, value, log):
    site,project = _credential_site(api,account_id,host)
    variable = 'ACCESS_PASSWORD' if field == 'access_password' else 'CACHE_PUBLISH_SECRET'
    # Journal before changing the remote project, so an interrupted deployment
    # never loses the supplied key. It is not presented as verified/current yet.
    _save_pending(site,field,value)
    configs = {name:{'env_vars':{variable:{'type':'secret_text','value':value} if value else None}}
               for name in project['deployment_configs']}
    try:
        api.patch(f"/accounts/{account_id}/pages/projects/{site['project']}", {'deployment_configs':configs})
        log('正在应用凭据修改到所选原站点…')
        redeploy_existing(api,account_id,site,log)
        from summary_publish import _post
        summary = site.get('kind') == 'summary'
        base = 'https://' + str(project.get('subdomain') or '')
        if not re.fullmatch(r'https://[a-z0-9-]+\.pages\.dev',base):
            raise CloudflareError('无法核验原站点 Pages 地址')
        if field == 'access_password':
            result = _post(base + ('/api/gallery' if summary else '/api/auth'), '',
                           {'action':'login','password':value}, lambda:False)
            if not result.get('ok') or (not summary and not result.get('authenticated')):
                raise CloudflareError('访问密码验证失败')
        else:
            result = _post(base + ('/api/summary-publish' if summary else '/api/publish-cache'),value,
                           {'action':'hello'},lambda:False)
            if result.get('service') != ('summary-gallery/v1' if summary else 'sheets-gallery-publish-cache'):
                raise CloudflareError('发布密钥验证失败')
        _finish_credential(site,field,value)
    except Exception as exc:
        raise CloudflareError('凭据修改尚未确认完成；新值已保存在本机“待确认修改”中。请重试应用并验证，勿按已成功处理。') from exc
    return {'ok':True,'site_url':site['site_url']}


def change_access_password(api, account_id, host, password, *, log=lambda _:None):
    if not isinstance(password,str): raise CloudflareError('访问密码必须是文本')
    return _apply_credential(api,account_id,host,'access_password',password,log)


def change_publish_secret(api, account_id, host, secret, *, log=lambda _:None):
    if not isinstance(secret,str) or len(secret.strip()) < 24:
        raise CloudflareError('发布密钥至少需要 24 个字符，可以点击“生成新密钥”后再应用。')
    return _apply_credential(api,account_id,host,'secret',secret.strip(),log)


def restore_publish_secret(api, account_id, host, secret, *, log=lambda _:None):
    return change_publish_secret(api,account_id,host,secret,log=log)
