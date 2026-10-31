"""Push merged image/video sheets directly to the Pages summary gallery (no Apps Script)."""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from collections import defaultdict
from datetime import timedelta
from urllib.parse import urlsplit, parse_qs, urlencode

import requests

from cf_accounts import site_for_menu
from fetch_posts import authorize_cfg, col_letter_to_index, open_by_url_or_id, to_datetime
from metric_sheet_io import MetricReader
from sheet_links import http_url, read_links
from worksheet_channels import stream_identity

_http_sessions = threading.local()


class GalleryPublishHTTPError(RuntimeError):
    def __init__(self, status, host):
        self.status = status
        hint = '，自定义域名尚未正确连接 Pages 或源服务超时' if status in (521,522,523,524,525,526) else ''
        super().__init__(f'Cloudflare 发布接口 {host} 返回 HTTP {status}{hint}')


class GalleryPublishConnectionError(RuntimeError):
    pass


def media_settings(cfg):
    kind = str(cfg.wm_media_type or 'image')
    if kind not in ('image', 'video'):
        raise ValueError('素材类型只能选择图片或视频')
    try:
        days = int(cfg.wm_release_days)
    except (ValueError, TypeError):
        raise ValueError('展示延后天数必须是 0 至 3650 的整数') from None
    if days < 0 or days > 3650:
        raise ValueError('展示延后天数必须是 0 至 3650 的整数')
    zone = str(cfg.wm_timezone or 'Europe/Lisbon').strip()
    if not re.fullmatch(r'[A-Za-z_+-]+(?:/[A-Za-z0-9_+.-]+)*', zone):
        raise ValueError('请填写有效的 IANA 时区，例如 Europe/Lisbon 或 Asia/Shanghai')
    direct = getattr(cfg,'wm_publish_source','merged') == 'direct'
    left = 0 if direct else col_letter_to_index(str(cfg.wm_target_start_col).strip().upper())
    width = getattr(cfg,'_wm_direct_width',18278) if direct else len(cfg.wm_columns or [])
    columns = {}
    for name, key, required in [('date', 'wm_date_col', True), ('link', 'wm_link_col', True),
                                 ('category', 'wm_category_col', True), ('title', 'wm_title_col', False),
                                 ('thumbnail', 'wm_thumbnail_col', False), ('maker', 'wm_maker_col', False)]:
        value = str(getattr(cfg, key, '') or '').strip().upper()
        if not value and not required:
            columns[name] = None
            continue
        if not re.fullmatch(r'[A-Z]+', value):
            raise ValueError(f'图库列配置 {key} 必须填写列字母')
        index = col_letter_to_index(value) - left
        # Existing short mappings keep working; a maker column outside the output
        # range simply has no value, until that column is included in the merge.
        if name == 'maker' and not 0 <= index < width:
            columns[name] = None
            continue
        if not 0 <= index < width:
            where = '直接发布 Sheet' if direct else '当前汇总结果'
            raise ValueError(f'图库列 {value} 不在{where}的列范围中，请调整图库列映射')
        columns[name] = index
    return kind, days, zone, columns


def drive_id(url):
    parsed = urlsplit(str(url or ''))
    if parsed.hostname not in ('drive.google.com', 'docs.google.com', 'lh3.googleusercontent.com'):
        return ''
    match = re.search(r'(?:/d/|[?&]id=)([\w-]{10,128})', str(url))
    return match[1] if match else ''


def download_url(url):
    file_id = drive_id(url)
    if not file_id:
        return url
    query = parse_qs(urlsplit(url).query)
    params = {'export':'download','id':file_id}
    key = (query.get('resourcekey') or query.get('resourceKey') or [''])[0]
    if key: params['resourcekey'] = key
    return 'https://drive.google.com/uc?' + urlencode(params)


def rows_to_summary_assets(cfg, rows, links=None):
    kind, days, zone, columns = media_settings(cfg)
    stream = hashlib.sha256(stream_identity(cfg).encode()).hexdigest()[:24]
    catalog_id = getattr(cfg,'_wm_catalog_id','') or ('videos' if kind=='video' else 'images')
    catalog_name = getattr(cfg,'_wm_channel_name','') or ('视频' if kind=='video' else '图片')
    assets, skipped, excluded = [], 0, 0
    raw_exclusions = getattr(cfg,'wm_gallery_exclude_categories',None) or []
    if isinstance(raw_exclusions,str):raw_exclusions=raw_exclusions.splitlines()
    exclusions = {str(value).strip() for value in raw_exclusions if str(value).strip()}
    links = links or [{} for _ in rows]
    buckets = defaultdict(lambda: defaultdict(int))
    for i, row in enumerate(rows):
        def value(key):
            index = columns[key]
            return str(row[index] or '').strip() if index is not None and index < len(row) else ''
        def url(key):
            return http_url(links[i].get(columns[key])) or http_url(value(key))
        link = url('link')
        date = to_datetime(value('date'))
        if not link or not date:
            if any(str(x).strip() for x in row):
                skipped += 1
            continue
        date_string = date.strftime('%Y-%m-%d')
        release = (date + timedelta(days=days)).strftime('%Y-%m-%d')
        category = value('category') or '未分类'
        if category in exclusions:
            excluded += 1
            continue
        file_id = drive_id(link)
        thumbnail = url('thumbnail') if columns['thumbnail'] is not None else ''
        if not thumbnail:
            thumbnail = f'https://drive.google.com/thumbnail?id={file_id}&sz=w1000' if file_id else (link if kind == 'image' else '')
        identity = hashlib.sha256(json.dumps([stream, i, link, date_string, category], ensure_ascii=False).encode()).hexdigest()[:32]
        assets.append({'id': identity, 'date': date_string, 'releaseDate': release,
                       'catalogId': catalog_id,
                       'group': catalog_name, 'type': category,
                       'mediaType': kind, 'title': value('title') or category, 'maker': value('maker'),
                       'fileId': file_id, 'sourceUrl': link, 'viewUrl': link,
                       'videoUrl': f'https://drive.google.com/file/d/{file_id}/preview' if file_id and kind == 'video' else link,
                       'previewUrl': thumbnail, 'image': thumbnail,
                       'downloadUrl': download_url(link)})
        buckets[release][category] += 1
    if rows and not assets and not excluded:
        raise ValueError('没有可发布的有效素材：请检查日期列、成品链接及超链接设置。旧网站数据未替换')
    return assets, {'mediaType': kind, 'delayDays': days, 'timezone': zone, 'catalogId':catalog_id, 'catalogName':catalog_name,
                    'releaseCounts': dict(buckets), 'excludedCategories':sorted(exclusions), 'excludedRows':excluded}, skipped


def _post(url, secret, body, cancelled):
    from secure_http import validate_https
    validate_https(url)
    if not getattr(_http_sessions, 'session', None):
        _http_sessions.session = requests.Session()
    for attempt in range(3):
        if cancelled():
            raise RuntimeError('任务已取消')
        try:
            response = _http_sessions.session.post(url, headers={'x-publish-secret': secret}, json=body, timeout=(8, 30), allow_redirects=False)
            if response.status_code in (429, 502, 503, 504) and attempt < 2:
                time.sleep(attempt + 1)
                continue
            if not response.ok:
                raise GalleryPublishHTTPError(response.status_code, urlsplit(url).hostname)
            if 300 <= response.status_code < 400:
                raise RuntimeError('发布接口发生重定向，已停止，避免将发布密钥转发到其他地址')
            result = response.json()
            if not result.get('ok'):
                raise RuntimeError(result.get('error') or '图库发布未完成')
            return result
        except (requests.ConnectionError, requests.Timeout):
            if attempt == 2:
                raise GalleryPublishConnectionError('Cloudflare 发布连接超时或中断') from None
            time.sleep(attempt + 1)


def _prepare_publish(cfg, *, log=print, cancelled=lambda: False):
    profile, site = site_for_menu(cfg.cf_profile_id, cfg.ui_active_menu, cfg.wm_gallery_host, kind='summary')
    url = site.get('publish_url') or f"https://{site['host']}/api/summary-publish"
    parsed = urlsplit(url)
    permitted_hosts = {site['host'], urlsplit(site.get('site_url') or '').hostname}
    if parsed.scheme != 'https' or parsed.hostname not in permitted_hosts or parsed.path != '/api/summary-publish':
        raise ValueError('汇总图库发布地址与所选站点不一致')
    secret = site.get('secret') or ''
    if not secret:
        raise ValueError('该站点没有保存发布密钥，请在账号管理中检查站点记录')
    # A protocol handshake prevents this data from replacing an older, unrelated gallery.
    log(f'检查图库发布接口：https://{parsed.hostname}…')
    try:
        hello = _post(url, secret, {'action': 'hello'}, cancelled)
    except (GalleryPublishHTTPError,GalleryPublishConnectionError) as exc:
        if isinstance(exc,GalleryPublishHTTPError) and exc.status not in (521,522,523,524,525,526):
            raise
        from promo_deploy import CloudflareAPI
        if not site.get('project') or not profile.get('account_id'):
            raise
        log('自定义域暂不可用，正在核验同一账号下的 Pages 项目备用地址…')
        api = CloudflareAPI(profile['api_token'])
        project = api.result(api.get(f"/accounts/{profile['account_id']}/pages/projects/{site['project']}")) or {}
        subdomain = str(project.get('subdomain') or '').lower()
        if project.get('name') != site['project'] or not subdomain.endswith('.pages.dev') or '/' in subdomain:
            raise ValueError('无法核验当前账号 Pages 项目的备用域名，未向其他地址发送密钥') from exc
        fallback = 'https://' + subdomain + '/api/summary-publish'
        if fallback == url:
            raise
        hello = _post(fallback,secret,{'action':'hello'},cancelled)
        url = fallback
        log(f'已切换到同一图库的 Pages 地址：{subdomain}；自定义域恢复后仍可正常展示')
    if hello.get('service') != 'summary-gallery/v1':
        raise ValueError('所选站点不是新的汇总图库接口，未上传或替换任何数据')
    return {'site':site,'url':url,'secret':secret,'features':hello.get('features') or []}


def publish_summary_rows(cfg, rows, links=None, *, log=print, cancelled=lambda: False, _context=None):
    context = _context or _prepare_publish(cfg,log=log,cancelled=cancelled)
    site,url,secret = context['site'],context['url'],context['secret']
    assets, metadata, skipped = rows_to_summary_assets(cfg, rows, links)
    if cfg.wm_maker_col and media_settings(cfg)[3]['maker'] is None:
        where = '直接发布 Sheet' if cfg.wm_publish_source=='direct' else '汇总输出'
        log(f'提示：制作人列 {cfg.wm_maker_col} 不在{where}的列范围，请检查制作人列映射')
    stream = hashlib.sha256(stream_identity(cfg).encode()).hexdigest()[:24]
    # Identical data/rules reuse the same private build keys instead of accumulating duplicate objects.
    build = hashlib.sha256(json.dumps([assets,metadata],ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()[:32]
    chunks = [assets[i:i+500] for i in range(0, len(assets), 500)]
    incremental = 'incremental-v1' in context.get('features', [])
    hashes = [hashlib.sha256(json.dumps(chunk,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()[:32] for chunk in chunks]
    reusable = set()
    if incremental:
        status = _post(url,secret,{'action':'status','stream':stream,'buildId':build},cancelled)
        if status.get('buildId') == build:
            log(f'图库数据和展示规则未变化，跳过上传：{len(assets)} 条（不重复写入 R2）')
            return {'ok':True,'skipped':True,'totalRows':len(assets),'stream':stream}
        reusable = set(status.get('chunkHashes') or [])
    log(f"软件直推图库：{len(assets)} 条，{len(chunks)} 个分片；无效日期或链接跳过 {skipped} 条；分类排除 {metadata.get('excludedRows',0)} 条")
    for i, chunk in enumerate(chunks, 1):
        if incremental and hashes[i-1] in reusable:
            log(f'复用已有图库分片 {i}/{len(chunks)}，无需上传')
            continue
        body = {'action': 'put-chunk', 'stream': stream, 'buildId': build, 'index': i, 'assets': chunk}
        if incremental: body['chunkHash'] = hashes[i-1]
        _post(url, secret, body, cancelled)
        log(f'已上传图库分片 {i}/{len(chunks)}')
    result = _post(url, secret, {'action': 'finalize', 'stream': stream, 'buildId': build,
                                'chunkCount': len(chunks), 'totalRows': len(assets), 'metadata': metadata,
                                **({'chunkHashes':hashes} if incremental else {})}, cancelled)
    log(f"图库发布完成：https://{site['host']}（当前配置独立更新，不覆盖其他图片/视频配置）")
    return result


def publish_summary_sheet(cfg, *, log=print, cancelled=lambda: False, _context=None):
    if getattr(cfg,'wm_channels',None):
        from worksheet_channels import publish_media_channels
        return publish_media_channels(cfg,log=log,cancelled=cancelled)
    if getattr(cfg,'wm_publish_source','merged') not in ('merged','direct'):
        raise ValueError('请选择汇总结果或直接读取表格作为发布来源')
    if cfg.wm_publish_source == 'direct':
        from summary_direct import validate_direct_config, read_direct_rows
        validate_direct_config(cfg); media_settings(cfg)
        context = _context or _prepare_publish(cfg,log=log,cancelled=cancelled)
        rows, links = read_direct_rows(cfg,log=log,cancelled=cancelled)
        return publish_summary_rows(cfg,rows,links,log=log,cancelled=cancelled,_context=context)
    from worksheet_merge import _a1, validate_merge_config
    spec = validate_merge_config(cfg)
    media_settings(cfg)
    context = _context or _prepare_publish(cfg,log=log,cancelled=cancelled)
    gc = authorize_cfg(cfg, log=log)
    book = open_by_url_or_id(gc, spec.target_url, log=log)
    worksheet = next((ws for ws in book.worksheets() if ws.title == spec.target_sheet), None)
    if worksheet is None:
        raise ValueError('目标 Sheet 不存在，请先执行汇总')
    reader = MetricReader(book, log=log, cancelled=cancelled)
    rows, links = [], []
    _kind, _days, _zone, columns = media_settings(cfg)
    absolute_links = [spec.target_col + columns['link']]
    if columns['thumbnail'] is not None:
        absolute_links.append(spec.target_col + columns['thumbnail'])
    for top in range(spec.target_start, worksheet.row_count + 1, 2000):
        bottom = min(worksheet.row_count, top + 1999)
        values = reader.read(_a1(worksheet.title, spec.target_col, top, spec.target_col + len(spec.columns) - 1, bottom),
                             render='FORMATTED_VALUE', what=f'读取待发布汇总 {top}-{bottom} 行')
        found = read_links(book, worksheet.title, top, bottom, absolute_links, values, spec.target_col, reader)
        link_rows = defaultdict(dict)
        for (offset, column), url in found.items():
            link_rows[offset][column-spec.target_col] = url
        for i, row in enumerate(values):
            if any(str(x).strip() for x in row):
                rows.append(row)
                links.append(link_rows.get(i, {}))
    return publish_summary_rows(cfg, rows, links, log=log, cancelled=cancelled, _context=context)
