"""Connect to existing galleries and change passwords without recreating resources."""
from pathlib import Path
import re
import time

from promo_deploy import (CloudflareError, known_gallery_sites, load_cf_state, normalize_host,
                         save_cf_state, _upload_assets, _create_deployment,
                         collect_static_files, rewrite_gallery_html, RESOURCE_DIR)


def find_project(api, account_id, host, *, project_hint=''):
    host = normalize_host(host)
    if project_hint:
        project = api.result(api.get(f'/accounts/{account_id}/pages/projects/{project_hint}'))
        if not owns_host(project, host):
            raise CloudflareError('已记录项目未绑定这个域名，已停止；请核对站点配置。')
        return project
    for page in range(1, 21):
        response = api.get(f'/accounts/{account_id}/pages/projects?per_page=100&page={page}')
        rows = api.result(response) or []
        matches = [row for row in rows if owns_host(row, host)]
        if len(matches) > 1:
            raise CloudflareError('此域名匹配多个项目，请导入原配置明确指定项目。')
        if matches:
            return api.result(api.get(f"/accounts/{account_id}/pages/projects/{matches[0]['name']}"))
        if len(rows) < 100:
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
    if kind != 'summary':
        raise CloudflareError('贴文图库请导入原配置复用，避免误识别并覆盖已有站点。')
    subdomain = str(project.get('subdomain') or '')
    if not re.fullmatch(r'[a-z0-9-]+\.pages\.dev', subdomain):
        raise CloudflareError('无法核验项目的固定 Pages 地址。')
    if not secret:
        raise CloudflareError('请导入原电脑配置，或填写原站点发布密钥；不会自动生成新密钥覆盖。')
    from summary_publish import _post
    hello = _post('https://' + subdomain + '/api/summary-publish', secret, {'action': 'hello'}, lambda: False)
    if hello.get('service') != 'summary-gallery/v1':
        raise CloudflareError('现有项目不是多工作表图库，未修改。')
    production = (project.get('deployment_configs') or {}).get('production') or {}
    bucket = (production.get('r2_buckets') or {}).get('GALLERY_CACHE', {}).get('name')
    if not bucket:
        raise CloudflareError('现有站点没有图库 R2 绑定，未修改。')
    state = load_cf_state()
    old = next((s for s in known_gallery_sites(state) if s['host'] == normalize_host(host)), {})
    site = {**old, 'host': normalize_host(host), 'account_id': account_id, 'profile_id': profile_id or account_id,
            'project': project['name'], 'bucket': bucket, 'kind': kind, 'secret': secret,
            'pages_url': 'https://' + subdomain, 'site_url': 'https://' + normalize_host(host),
            'publish_url': 'https://' + normalize_host(host) + '/api/summary-publish'}
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
        headers=(folder / '_headers').read_bytes() if (folder / '_headers').exists() else None)
    for _ in range(20):
        current = api.result(api.get(f"/accounts/{account_id}/pages/projects/{site['project']}/deployments/{deployment['id']}"))
        stage = current.get('latest_stage') or {}
        if stage.get('status') == 'success' and stage.get('name') == 'deploy':
            return deployment
        if stage.get('status') in ('failure', 'canceled'):
            raise CloudflareError('原项目的部署未成功，请在 Cloudflare 查看记录；没有创建新站点。')
        time.sleep(1)
    raise CloudflareError('设置已保存，原项目部署仍在处理，请稍后重试访问固定域名。')


def change_access_password(api, account_id, host, password, *, log=lambda _: None):
    host = normalize_host(host)
    state = load_cf_state()
    site = next((s for s in known_gallery_sites(state) if s['host'] == host and s['account_id'] == account_id), None)
    if not site:
        raise CloudflareError('请先选择 / 导入现有站点配置，再修改访问密码。')
    project = find_project(api, account_id, host, project_hint=site['project'])
    validate_project(project, site)
    # Match Wrangler pages/secret/index.ts: PATCH upserts only the named
    # variable. Never echo redacted secret values from a GET back into a PATCH.
    configs = {name: {'env_vars': {'ACCESS_PASSWORD': {'type': 'secret_text', 'value': password} if password else None},
                      'wrangler_config_hash': config.get('wrangler_config_hash')}
               for name, config in project['deployment_configs'].items()}
    api.patch(f"/accounts/{account_id}/pages/projects/{site['project']}", {'deployment_configs': configs})
    log('访问密码设置已保存，正在应用到原项目；发布密钥、素材数据和 DNS 保持原配置。')
    redeploy_existing(api, account_id, site, log)
    # Verify the password before persisting the local record. Do not log login tokens.
    from summary_publish import _post
    if site.get('kind') == 'summary':
        result = _post(site['pages_url'] + '/api/gallery', '', {'action': 'login', 'password': password}, lambda: False)
        if not result.get('ok'):
            raise CloudflareError('原项目已部署，但访问密码验证失败；请检查线上设置。')
    latest = load_cf_state()
    sites = known_gallery_sites(latest)
    for row in sites:
        if row['host'] == host:
            row['access_password'] = password
    updates = {'sites': sites}
    if latest.get('host') == host:
        updates['access_password'] = password
    save_cf_state(updates)
    return {'ok': True, 'site_url': site['site_url']}


def restore_publish_secret(api, account_id, host, secret, *, log=lambda _: None):
    """Explicit recovery using an original key supplied by the maintainer, never generation."""
    if not isinstance(secret, str) or len(secret.strip()) < 24:
        raise CloudflareError('请先导入原电脑配置，或填写原站点发布密钥（至少 24 个字符）。')
    secret = secret.strip()
    host = normalize_host(host)
    state = load_cf_state()
    site = next((s for s in known_gallery_sites(state) if s['host'] == host and s['account_id'] == account_id), None)
    if not site or site.get('kind') != 'summary':
        raise CloudflareError('请先导入对应的多工作表图库站点记录。')
    project = find_project(api, account_id, host, project_hint=site['project'])
    validate_project(project, site)
    configs = {name: {'env_vars': {'CACHE_PUBLISH_SECRET': {'type': 'secret_text', 'value': secret}}}
               for name in project['deployment_configs']}
    api.patch(f"/accounts/{account_id}/pages/projects/{site['project']}", {'deployment_configs': configs})
    log('正在将提供的原发布密钥应用到原项目，访问密码和素材保持原配置。')
    redeploy_existing(api, account_id, site, log)
    from summary_publish import _post
    hello = _post(site['pages_url'] + '/api/summary-publish', secret, {'action': 'hello'}, lambda: False)
    if hello.get('service') != 'summary-gallery/v1':
        raise CloudflareError('原项目已部署，但发布接口验证未通过，请检查现有站点。')
    latest = load_cf_state()
    sites = known_gallery_sites(latest)
    for row in sites:
        if row['host'] == host:
            row['secret'] = secret
    updates = {'sites': sites}
    if latest.get('host') == host:
        updates['secret'] = secret
    save_cf_state(updates)
    return {'ok': True, 'site_url': site['site_url']}
