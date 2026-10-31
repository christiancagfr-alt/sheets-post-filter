"""One menu contains independent image/video settings and several source workbooks."""
from copy import deepcopy
from dataclasses import asdict

from fetch_posts import Config, extract_spreadsheet_id

KINDS = ('image','video','effect_video')
NAMES = {'image':'图片','video':'KB视频','effect_video':'效果视频'}
MEDIA_TYPES = {'image':'image','video':'video','effect_video':'video'}
CATALOG_IDS = {'image':'images','video':'kb_videos','effect_video':'effect_videos'}
SHARED_FIELDS = {'wm_target_url','cf_profile_id','wm_gallery_host','wm_publish_after_sync',
                 'wm_schedule_enabled','wm_schedule_minutes','wm_schedule_merge','wm_schedule_publish'}
CHANNEL_FIELDS = (
    'wm_source_url','wm_source_urls','wm_source_header_row','wm_source_start_row','wm_exclude_sheets','wm_sheet_filter_mode','wm_columns',
    'wm_target_sheet','wm_target_header_row','wm_target_start_row','wm_target_start_col',
    'wm_media_type','wm_release_days','wm_timezone','wm_date_col','wm_link_col','wm_category_col','wm_title_col','wm_maker_col','wm_thumbnail_col',
    'wm_publish_source','wm_direct_url','wm_direct_sheet','wm_direct_header_row','wm_direct_start_row','wm_direct_columns',
    'wm_gallery_exclude_categories',
)


def source_urls(cfg):
    raw = getattr(cfg,'wm_source_urls',None) or [getattr(cfg,'wm_source_url','')]
    if isinstance(raw,str): raw=raw.splitlines()
    result,seen = [],set()
    for value in raw:
        url=str(value or '').strip()
        if not url:continue
        identity=extract_spreadsheet_id(url)
        if not identity:raise ValueError('来源列表中有无效的 Google 表格链接，请逐条检查')
        if identity not in seen:
            result.append(url);seen.add(identity)
    return result


def normalize_channels(settings):
    defaults=asdict(Config())
    stored={row.get('id'):row for row in settings.get('wm_channels') or [] if isinstance(row,dict) and row.get('id') in KINDS}
    legacy_kind=settings.get('wm_media_type') if settings.get('wm_media_type') in KINDS else 'image'
    has_legacy=bool(settings.get('wm_source_url') or settings.get('wm_source_urls') or settings.get('wm_columns'))
    result=[]
    for kind in KINDS:
        values={key:deepcopy(defaults[key]) for key in CHANNEL_FIELDS}
        values.update(wm_media_type=MEDIA_TYPES[kind],wm_release_days=4 if kind=='image' else 7,wm_target_sheet=NAMES[kind]+'汇总')
        row=stored.get(kind)
        legacy=False
        if row:
            values.update({key:deepcopy(value) for key,value in (row.get('settings') or {}).items() if key in CHANNEL_FIELDS})
            enabled=bool(row.get('enabled'))
            legacy=bool(row.get('legacy_stream'))
        elif not stored and has_legacy and kind==legacy_kind:
            values.update({key:deepcopy(settings[key]) for key in CHANNEL_FIELDS if key in settings})
            enabled=True;legacy=True
        else:
            enabled=not stored and not has_legacy and kind=='image'
        values['wm_media_type']=MEDIA_TYPES[kind]
        if not values.get('wm_source_urls') and values.get('wm_source_url'):
            values['wm_source_urls']=[values['wm_source_url']]
        result.append({'id':kind,'name':NAMES[kind],'enabled':enabled,'legacy_stream':legacy,'settings':values})
    return result


def channel_config(cfg, channel):
    child=deepcopy(cfg)
    child.wm_channels=[]
    for key,value in channel['settings'].items():
        if key in CHANNEL_FIELDS:setattr(child,key,deepcopy(value))
    child.wm_media_type=MEDIA_TYPES[channel['id']]
    child._wm_stream_suffix='' if channel.get('legacy_stream') else channel['id']
    child._wm_channel_name=NAMES[channel['id']]
    child._wm_catalog_id=CATALOG_IDS[channel['id']]
    all_channels=normalize_channels(asdict(cfg))
    child._wm_output_sheet_names={str(row['settings'].get('wm_target_sheet') or '').strip() for row in all_channels}
    return child


def merge_jobs(cfg, *, publishing=False):
    if not getattr(cfg,'wm_channels',None):
        if not publishing and getattr(cfg,'wm_publish_source','merged')=='direct':
            raise ValueError('直接发布模式无需汇总，请点击发布图库')
        return [(None,cfg)]
    rows=normalize_channels(asdict(cfg))
    jobs=[(row['id'],channel_config(cfg,row)) for row in rows if row['enabled'] and (publishing or row['settings'].get('wm_publish_source')!='direct')]
    if not jobs:raise ValueError('请至少启用一个类别；直接发布模式无需汇总，请点击发布图库')
    destinations=[]
    for _kind,child in jobs:
        if getattr(child,'wm_publish_source','merged')=='direct':continue
        identity=(extract_spreadsheet_id(child.wm_target_url),str(child.wm_target_sheet).strip())
        if identity in destinations:raise ValueError('不同类别必须写入不同的目标 Sheet，请修改工作表名称')
        destinations.append(identity)
    return jobs


def validate_merge_jobs(cfg):
    from worksheet_merge import validate_merge_config
    jobs=merge_jobs(cfg)
    for kind,child in jobs:
        try:validate_merge_config(child)
        except (TypeError,ValueError) as exc:
            raise ValueError(f'{NAMES[kind]}配置：{exc}' if kind else str(exc)) from exc
    return jobs


def stream_identity(cfg):
    suffix=str(getattr(cfg,'_wm_stream_suffix','') or '')
    return str(cfg.ui_active_menu)+(f'|{suffix}' if suffix else '')


def validate_publish_jobs(cfg):
    from summary_publish import media_settings
    from summary_direct import validate_direct_config
    from worksheet_merge import validate_merge_config
    jobs=merge_jobs(cfg,publishing=True)
    for kind,child in jobs:
        try:
            if child.wm_publish_source not in ('merged','direct'):raise ValueError('请选择有效的发布来源模式')
            if child.wm_publish_source=='direct':validate_direct_config(child)
            else:validate_merge_config(child)
            media_settings(child)
        except (TypeError,ValueError) as exc:
            raise ValueError(f'{NAMES[kind]}配置：{exc}' if kind else str(exc)) from exc
    return jobs


def run_media_merge(cfg, *, log=print, cancelled=lambda:False):
    from worksheet_merge import run_worksheet_merge
    jobs=validate_merge_jobs(cfg)
    results={}
    for kind,child in jobs:
        if cancelled():raise RuntimeError('任务已取消')
        log(f'开始{NAMES[kind]}汇总：{len(source_urls(child))} 个来源表格 → {child.wm_target_sheet}')
        try:results[kind]=run_worksheet_merge(child,log=log,cancelled=cancelled)
        except Exception as exc:raise RuntimeError(f'{NAMES[kind]}汇总失败：{exc}；已完成类别：{",".join(NAMES[key] for key in results) or "无"}') from exc
    return {'mode':'worksheet_media_merge','channels':results,'total_rows':sum(row.get('total_rows',0) for row in results.values())}


def publish_media_channels(cfg, *, log=print, cancelled=lambda:False):
    from summary_publish import _prepare_publish,publish_summary_sheet
    jobs=validate_publish_jobs(cfg)
    context=_prepare_publish(cfg,log=log,cancelled=cancelled)
    results={}
    for kind,child in jobs:
        if cancelled():raise RuntimeError('任务已取消')
        title=child.wm_direct_sheet or '链接指定工作表' if child.wm_publish_source=='direct' else child.wm_target_sheet
        log(f'发布{NAMES[kind]}：读取 {title}，延后 {child.wm_release_days} 天展示')
        results[kind]=publish_summary_sheet(child,log=log,cancelled=cancelled,_context=context)
    return {'ok':True,'mode':'worksheet_media_publish','channels':results,'totalRows':sum(row.get('totalRows',0) for row in results.values())}


def validate_scheduled_media_config(cfg):
    try:minutes=int(cfg.wm_schedule_minutes)
    except (ValueError,TypeError):raise ValueError('定时间隔必须填写整数分钟') from None
    if minutes<1 or minutes>525600:raise ValueError('定时间隔需为 1 至 525600 分钟')
    merge=bool(cfg.wm_schedule_merge);publish=bool(cfg.wm_schedule_publish)
    if not merge and not publish:raise ValueError('请至少勾选定时汇总或定时发布')
    jobs=validate_publish_jobs(cfg) if publish else validate_merge_jobs(cfg)
    return minutes,jobs


def run_scheduled_media_sync(cfg, *, log=print, cancelled=lambda:False):
    """One menu job: merge then publish per category, or publish a direct source."""
    from worksheet_merge import run_worksheet_merge
    from summary_publish import publish_summary_sheet
    _minutes,jobs=validate_scheduled_media_config(cfg)
    results={};merged_rows=published_rows=0
    for kind,child in jobs:
        if cancelled():raise RuntimeError('任务已取消')
        name=NAMES.get(kind,'当前配置')
        child.wm_publish_after_sync=False
        outcome={}
        try:
            if cfg.wm_schedule_merge and child.wm_publish_source!='direct':
                log(f'定时汇总{name}，完成后'+('发布图库' if cfg.wm_schedule_publish else '结束本轮'))
                # Reuse freshly merged rows for publication, avoiding a second
                # read of the full target sheet and preventing duplicate publishes.
                child.wm_publish_after_sync=bool(cfg.wm_schedule_publish)
                result=run_worksheet_merge(child,log=log,cancelled=cancelled)
                outcome['merge']=result;merged_rows+=result.get('total_rows',0)
                if cfg.wm_schedule_publish:
                    outcome['publish']=result.get('published') or {}
                    published_rows+=outcome['publish'].get('totalRows',0)
            elif cfg.wm_schedule_publish:
                if child.wm_publish_source=='direct':log(f'{name}为直接发布模式，跳过汇总，读取指定表格并发布')
                else:log(f'定时发布{name}现有汇总结果')
                outcome['publish']=publish_summary_sheet(child,log=log,cancelled=cancelled)
                published_rows+=outcome['publish'].get('totalRows',0)
            results[kind or 'current']=outcome
        except Exception as exc:
            completed='、'.join(NAMES.get(key,key) for key in results) or '无'
            raise RuntimeError(f'{name}定时执行失败：{exc}；已完成类别：{completed}') from exc
    return {'mode':'worksheet_sync','channels':results,'total_rows':published_rows if cfg.wm_schedule_publish else merged_rows,
            'merged_rows':merged_rows,'published_rows':published_rows}


def repair_media_channels(cfg, *, log=print, cancelled=lambda:False):
    from worksheet_merge import repair_worksheet_links
    results={}
    for kind,child in validate_merge_jobs(cfg):
        log(f'修复{NAMES[kind]}汇总表的超链接…')
        results[kind]=repair_worksheet_links(child,log=log,cancelled=cancelled)
    return {'mode':'worksheet_link_repair','channels':results,'total_rows':sum(row.get('total_rows',0) for row in results.values()),
            'unmatched':sum(row.get('unmatched',0) for row in results.values()),'ambiguous':sum(row.get('ambiguous',0) for row in results.values())}
