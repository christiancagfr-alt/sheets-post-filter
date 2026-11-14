"""Per-project analytics: bounded read queries, local cache, no gallery R2 writes."""
import hashlib
import re
import time
from datetime import datetime, timezone
from collections import defaultdict
from promo_deploy import CloudflareError

_CACHE = {}


def dataset_name(project):
    return 'gallery_' + hashlib.sha256(str(project).encode()).hexdigest()[:24]


def ensure_binding(api, account_id, project):
    """Add only the analytics binding, retaining all existing environment settings."""
    configs = {}
    for mode in ('production', 'preview'):
        existing = (project.get('deployment_configs') or {}).get(mode) or {}
        bindings = dict(existing.get('analytics_engine_datasets') or {})
        expected = dataset_name(project['name'])
        if bindings.get('GALLERY_ANALYTICS'):
            if bindings['GALLERY_ANALYTICS'].get('dataset') != expected:
                raise CloudflareError('访问分析绑定与当前项目不一致，未覆盖，请检查项目配置')
            continue
        bindings['GALLERY_ANALYTICS'] = {'dataset': expected}
        configs[mode] = {'analytics_engine_datasets': bindings}
    if configs:
        api.patch(f"/accounts/{account_id}/pages/projects/{project['name']}", {'deployment_configs': configs})


def aggregate_visits(rows):
    days, hours = {}, {}
    for row in rows:
        stamp = datetime.fromtimestamp(float(row['started']), timezone.utc)
        duration = max(0, min(28800, float(row['seconds'])))
        weight = max(1, float(row.get('weight') or 1))
        for group, key in ((days, stamp.strftime('%Y-%m-%d')), (hours, stamp.strftime('%Y-%m-%d %H:00'))):
            item = group.setdefault(key, {'visitors': set(), 'visits': 0, 'seconds': 0})
            item['visitors'].add(row['visitor'])
            item['visits'] += weight
            item['seconds'] += duration * weight
    def finish(group):
        return [{'period': key, 'visitors': len(value['visitors']), 'visits': round(value['visits']),
                 'average_seconds': round(value['seconds']/value['visits'], 1)}
                for key, value in sorted(group.items(), reverse=True)]
    return {'days': finish(days), 'hours': finish(hours),
            'sampled': any(float(row.get('weight') or 1) > 1 for row in rows)}


def read_analytics(api, account_id, site, days=7):
    days = int(days)
    if days not in (1, 7, 30): raise ValueError('请选择 1、7 或 30 天')
    from gallery_management import find_project, validate_project
    key = (account_id, site['project'], site['host'], days)
    cached = _CACHE.get(key)
    if cached and time.monotonic()-cached[0] < 300:
        return {**cached[1], 'cached': True}
    project = find_project(api, account_id, site['host'], project_hint=site['project'])
    config = validate_project(project, site)
    dataset = ((config.get('analytics_engine_datasets') or {}).get('GALLERY_ANALYTICS') or {}).get('dataset')
    if dataset != dataset_name(site['project']):
        raise ValueError('这个网站尚未启用访问分析，请先点击“更新所选网站程序”。历史访问无法补录。')
    from urllib.parse import urlsplit
    hosts = {site['host'], urlsplit(site.get('pages_url') or '').hostname}
    hosts.discard(None)
    if any(not re.fullmatch(r'[a-z0-9.-]+', host) for host in hosts):
        raise ValueError('网站地址无效')
    host_filter = ','.join("'" + host + "'" for host in sorted(hosts))
    sql = f'''SELECT blob1 AS visitor, blob2 AS session, double1 AS seconds,
        double2 AS started, _sample_interval AS weight
        FROM events.analyticsEngine.{dataset}
        WHERE timestamp >= NOW() - INTERVAL '{days}' DAY AND index1 IN ({host_filter})
        LIMIT 20001 FORMAT JSON'''
    try:
        response = api.post(f'/accounts/{account_id}/analytics_engine/sql', raw_body=sql)
    except CloudflareError as exc:
        if exc.status in (401,403):
            raise ValueError('读取访问分析需要账号 Token 的 Account Analytics / Read 权限，请在“账号与权限”更新 Token。') from exc
        raise
    if not isinstance(response.get('data'), list):
        raise ValueError('访问分析尚无可读取的数据或接口返回异常；部署后请访问网站并离开页面，再稍后查询。')
    rows = response['data']
    if len(rows) > 20000:
        raise ValueError('这个时间范围的访问记录较多，请缩小到 1 天查询，避免显示不完整数据。')
    result = {**aggregate_visits(rows), 'cached': False,
              'updated_at': datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
    if len(_CACHE) >= 64: _CACHE.pop(next(iter(_CACHE)))
    _CACHE[key] = (time.monotonic(), result)
    return result
