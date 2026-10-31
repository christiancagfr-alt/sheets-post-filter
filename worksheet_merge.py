"""Merge visible worksheets into one configured destination, copying displayed text."""
from __future__ import annotations

import re
from dataclasses import dataclass

from fetch_posts import authorize_cfg, col_letter_to_index, extract_spreadsheet_id, index_to_col_letter, open_by_url_or_id
from metric_sheet_io import MetricReader
from sheet_links import is_link_column, read_links, write_links
from worksheet_channels import source_urls


@dataclass
class MergeSpec:
    source_url: str
    target_url: str
    target_sheet: str
    header_row: int
    source_start: int
    target_header: int
    target_start: int
    target_col: int
    columns: list[dict]
    exclusions: set[str]
    sheet_filter_mode: str = 'exclude'


def sheet_selection(cfg):
    mode = str(getattr(cfg, 'wm_sheet_filter_mode', 'exclude') or 'exclude')
    if mode not in ('exclude', 'include'):
        raise ValueError('请选择有效的子 Sheet 读取模式')
    names = {str(x).strip() for x in cfg.wm_exclude_sheets or [] if str(x).strip()}
    if mode == 'include' and not names:
        raise ValueError('选择“只读取列出的子 Sheet”时，请至少填写一个完整的子 Sheet 名称')
    return names, mode


def validate_merge_config(cfg):
    def positive(key):
        try:
            value = int(getattr(cfg, key))
        except (TypeError, ValueError):
            raise ValueError("来源和目标的行号必须是正整数") from None
        if value < 1:
            raise ValueError("来源和目标的行号必须大于 0")
        return value

    urls = source_urls(cfg)
    source = urls[0] if urls else ''
    target = str(cfg.wm_target_url or "").strip()
    title = str(cfg.wm_target_sheet or "").strip()
    if not source or not target or not title:
        raise ValueError("请填写来源表格链接、目标表格链接和目标 Sheet 名称")
    if not extract_spreadsheet_id(source) or not extract_spreadsheet_id(target):
        raise ValueError("来源和目标必须填写有效的 Google 表格链接或表格 ID")
    header, start = positive("wm_source_header_row"), positive("wm_source_start_row")
    output_header, output_start = positive("wm_target_header_row"), positive("wm_target_start_row")
    if start <= header or output_start <= output_header:
        raise ValueError("来源和目标的数据起始行都必须在各自表头行之后")
    column = str(cfg.wm_target_start_col or "").strip().upper()
    if not re.fullmatch(r"[A-Z]+", column) or col_letter_to_index(column) >= 18278:
        raise ValueError("目标起始列应填写 A 至 ZZZ 的列字母")
    columns = []
    for item in cfg.wm_columns or []:
        if not isinstance(item, dict):
            raise ValueError("汇总字段配置无效，请重新添加字段")
        name = str(item.get("header") or "").strip()
        source_col = str(item.get("column") or "").strip().upper()
        if not name or not re.fullmatch(r"[A-Z]+", source_col):
            raise ValueError("每个字段都需要填写汇总表头和来源列字母")
        index = col_letter_to_index(source_col)
        if index >= 18278:
            raise ValueError("来源列超出 Google 表格支持的范围")
        columns.append({"header": name, "index": index, "hyperlink": is_link_column(item)})
    if not columns:
        raise ValueError("请至少配置一个汇总字段，可手动添加或读取来源表头")
    if len({item['header'] for item in columns}) != len(columns):
        raise ValueError("汇总表头不能重名")
    if col_letter_to_index(column) + len(columns) > 18278:
        raise ValueError("目标列数超出 Google 表格支持的范围")
    names, mode = sheet_selection(cfg)
    return MergeSpec(source, target, title, header, start, output_header, output_start,
                     col_letter_to_index(column), columns, names, mode)


def select_worksheets(worksheets, exclusions, target_id=None, *, mode='exclude'):
    return [ws for ws in worksheets if not (getattr(ws, '_properties', {}) or {}).get('hidden')
            and ((ws.title in exclusions) if mode == 'include' else (ws.title not in exclusions)) and ws.id != target_id]


def _a1(title, left, top, right, bottom):
    return "'" + title.replace("'", "''") + "'!" + index_to_col_letter(left) + str(top) + ":" + index_to_col_letter(right) + str(bottom)


def _open(cfg, log, cancelled):
    gc = authorize_cfg(cfg, log=log)
    if cancelled():
        raise RuntimeError("任务已取消")
    urls = source_urls(cfg)
    if not urls:raise ValueError('请至少添加一个来源表格链接')
    source = open_by_url_or_id(gc, urls[0], log=log)
    return gc, source


def _open_merge_books(cfg,spec,log,cancelled):
    gc,first=_open(cfg,log,cancelled)
    urls=source_urls(cfg)
    sources=[(urls[0],first)]
    cache={extract_spreadsheet_id(urls[0]):first}
    for url in urls[1:]:
        if cancelled():raise RuntimeError('任务已取消')
        book=open_by_url_or_id(gc,url,log=log)
        cache[extract_spreadsheet_id(url)]=book
        sources.append((url,book))
    target=cache.get(extract_spreadsheet_id(spec.target_url))
    if target is None:target=open_by_url_or_id(gc,spec.target_url,log=log)
    return gc,sources,target


def _source_sheets(cfg,spec,sources,target,target_tabs):
    selected=[]
    outputs=getattr(cfg,'_wm_output_sheet_names',None) or {spec.target_sheet}
    for number,(url,book) in enumerate(sources,1):
        same=extract_spreadsheet_id(url)==extract_spreadsheet_id(spec.target_url)
        tabs=target_tabs if book is target else book.worksheets()
        for ws in select_worksheets(tabs,spec.exclusions,mode=spec.sheet_filter_mode):
            if same and ws.title in outputs:continue
            selected.append((book,ws,number))
    return selected


def read_merge_headers(cfg, log=lambda _message: None):
    """Import names/column positions from the first eligible sheet; no writes."""
    urls=source_urls(cfg)
    if not urls:
        raise ValueError("请先填写有效的来源表格链接")
    row = int(cfg.wm_source_header_row)
    if row < 1:
        raise ValueError("来源表头行必须大于 0")
    names, mode = sheet_selection(cfg)
    gc, source = _open(cfg, log, lambda: False)
    selected=[]
    outputs=getattr(cfg,'_wm_output_sheet_names',None) or {cfg.wm_target_sheet}
    for index,url in enumerate(urls):
        if index:source=open_by_url_or_id(gc,url,log=log)
        same=extract_spreadsheet_id(url)==extract_spreadsheet_id(cfg.wm_target_url)
        selected=[ws for ws in select_worksheets(source.worksheets(),names,mode=mode) if not (same and ws.title in outputs)]
        if selected:break
    if not selected:
        raise ValueError("没有可读取的子 Sheet；请检查读取模式和名称名单（隐藏及目标 Sheet 均跳过）")
    ws = selected[0]
    if row > ws.row_count:
        raise ValueError("来源表头行超出工作表行数")
    reader = MetricReader(source, log=log)
    values = reader.read(_a1(ws.title, 0, row, ws.col_count - 1, row), render='FORMATTED_VALUE', what='读取来源表头')
    columns = [{"header": str(value).strip(), "column": index_to_col_letter(i), "hyperlink": is_link_column({'header': str(value)})}
               for i, value in enumerate(values[0] if values else []) if str(value).strip()]
    if not columns:
        raise ValueError("指定行没有表头，请检查来源表头行")
    return columns


def run_worksheet_merge(cfg, *, log=print, cancelled=lambda: False):
    if getattr(cfg,'wm_channels',None):
        from worksheet_channels import run_media_merge
        return run_media_merge(cfg,log=log,cancelled=cancelled)
    spec = validate_merge_config(cfg)
    gc,sources,target=_open_merge_books(cfg,spec,log,cancelled)
    target_tabs=target.worksheets()
    destination = next((ws for ws in target_tabs if ws.title == spec.target_sheet), None)
    selected=_source_sheets(cfg,spec,sources,target,target_tabs)
    if not selected:
        raise ValueError("没有可汇总的子 Sheet；请检查读取模式和名称名单。隐藏工作表和目标 Sheet 会自动跳过")
    reader = MetricReader(sources[0][1], log=log, cancelled=cancelled)
    output = []
    output_links = []
    left, right = min(x['index'] for x in spec.columns), max(x['index'] for x in spec.columns)
    for i, (source,ws,source_number) in enumerate(selected, 1):
        reader=MetricReader(source,log=log,cancelled=cancelled)
        reader.check_cancelled()
        log(f"[{i}/{len(selected)}] 来源表格 {source_number}/{len(sources)}，正在读取「{ws.title}」…")
        if spec.header_row > ws.row_count:
            raise ValueError(f"「{ws.title}」没有配置的表头行，目标表未修改")
        if right >= ws.col_count:
            raise ValueError(f"「{ws.title}」没有配置的来源列 {index_to_col_letter(right)}，目标表未修改")
        count = 0
        for start in range(spec.source_start, ws.row_count + 1, 2000):
            end = min(ws.row_count, start + 1999)
            rows = reader.read(_a1(ws.title, left, start, right, end), render='FORMATTED_VALUE', what=f'读取「{ws.title}」{start}-{end} 行')
            links = read_links(source, ws.title, start, end, [c['index'] for c in spec.columns if c['hyperlink']], rows, left, reader)
            for offset, row in enumerate(rows):
                mapped = [str(row[c['index'] - left]) if c['index'] - left < len(row) else '' for c in spec.columns]
                if any(value.strip() for value in mapped):
                    output.append(mapped)
                    output_links.append({i: links[offset, c['index']] for i, c in enumerate(spec.columns) if (offset, c['index']) in links})
                    count += 1
        log(f"[{i}/{len(selected)}] 「{ws.title}」读取完成：{count} 行")
    reader.check_cancelled()
    # Finish source reads before replacing anything, so a missing source never silently wipes prior results.
    width = len(spec.columns)
    end_row = max(spec.target_header, spec.target_start + len(output) - 1)
    end_col = spec.target_col + width
    writer = MetricReader(target, log=log, cancelled=cancelled)
    if destination is None:
        destination = writer.execute(lambda: target.add_worksheet(title=spec.target_sheet, rows=max(100, end_row), cols=max(26, end_col)), what='创建目标 Sheet')
    elif destination.row_count < end_row or destination.col_count < end_col:
        writer.execute(lambda: destination.resize(rows=max(destination.row_count, end_row), cols=max(destination.col_count, end_col)), what='扩展目标 Sheet')
    start = 0
    while start < len(output):
        chunk = output[start:start + 1000]
        batch = [{'range': _a1(spec.target_sheet, spec.target_col, spec.target_start + start,
                               end_col - 1, spec.target_start + start + len(chunk) - 1), 'values': chunk}]
        writer.write(batch, input_option='RAW', what=f'写入汇总数据 {start + 1}-{start + len(chunk)}/{len(output)}')
        start += len(chunk)
    writer.write([{'range': _a1(spec.target_sheet, spec.target_col, spec.target_header, end_col - 1, spec.target_header),
                   'values': [[c['header'] for c in spec.columns]]}], input_option='RAW', what='写入汇总表头')
    write_links(target, destination, spec.target_start, output, output_links,
                [(i, spec.target_col + i) for i, c in enumerate(spec.columns) if c['hyperlink']], writer)
    # Clear values only in the configured result columns, never formatting or neighboring data.
    tail = spec.target_start + len(output)
    if tail <= destination.row_count:
        writer.execute(lambda: target.values_batch_clear({'ranges': [_a1(spec.target_sheet, spec.target_col, tail, end_col - 1, destination.row_count)]}), what='清理上次汇总的多余数据')
    log(f"多工作表汇总完成：{len(selected)} 个子 Sheet，共 {len(output)} 行。链接列保留可点击链接，其他列保留显示文字及目标样式")
    result = {'mode': 'worksheet_merge', 'total_rows': len(output), 'ok_sheets': len(selected)}
    if getattr(cfg, 'wm_publish_after_sync', False):
        from summary_publish import publish_summary_rows
        try:
            result['published'] = publish_summary_rows(cfg, output, output_links, log=log, cancelled=cancelled)
        except Exception as exc:
            raise RuntimeError(f'表格汇总已完成，但图库发布未完成：{str(exc).strip() or type(exc).__name__}') from exc
    return result


def repair_worksheet_links(cfg, *, log=print, cancelled=lambda: False):
    """Restore only link metadata on rows that still exactly match source values."""
    if getattr(cfg,'wm_channels',None):
        from worksheet_channels import repair_media_channels
        return repair_media_channels(cfg,log=log,cancelled=cancelled)
    spec = validate_merge_config(cfg)
    link_fields = [(i,c) for i,c in enumerate(spec.columns) if c['hyperlink']]
    if not link_fields:
        raise ValueError('请先在字段配置中勾选需要保留超链接的列')
    gc,sources,target=_open_merge_books(cfg,spec,log,cancelled)
    target_tabs=target.worksheets()
    destination = next((ws for ws in target_tabs if ws.title == spec.target_sheet), None)
    if destination is None:
        raise ValueError('目标 Sheet 不存在，请先执行汇总')
    selected=_source_sheets(cfg,spec,sources,target,target_tabs)
    reader = MetricReader(sources[0][1], log=log, cancelled=cancelled)
    writer = MetricReader(target, log=log, cancelled=cancelled)
    left, right = min(c['index'] for c in spec.columns), max(c['index'] for c in spec.columns)
    source_index = {}
    source_sequence = []
    label_index = {}
    for number,(source,ws,source_number) in enumerate(selected, 1):
        reader=MetricReader(source,log=log,cancelled=cancelled)
        log(f'[{number}/{len(selected)}] 核对来源超链接「{ws.title}」…')
        if right >= ws.col_count:
            raise ValueError(f'「{ws.title}」没有配置的来源列，未开始修复目标')
        for top in range(spec.source_start, ws.row_count + 1, 2000):
            bottom = min(ws.row_count, top + 1999)
            values = reader.read(_a1(ws.title,left,top,right,bottom),render='FORMATTED_VALUE',what=f'核对「{ws.title}」{top}-{bottom} 行')
            found = read_links(source,ws.title,top,bottom,[c['index'] for _i,c in link_fields],values,left,reader)
            for offset, row in enumerate(values):
                key = tuple(str(row[c['index']-left]) if c['index']-left<len(row) else '' for c in spec.columns)
                if not any(value.strip() for value in key):
                    continue
                row_links = {relative:found[offset,column['index']] for relative,column in link_fields if (offset,column['index']) in found}
                source_sequence.append((key,row_links))
                source_index.setdefault(key,{})
                for relative, column in link_fields:
                    uri = found.get((offset,column['index']))
                    if uri:
                        source_index.setdefault(key,{}).setdefault(relative,set()).add(uri)
                        label_index.setdefault((relative,key[relative]),set()).add(uri)
    fixed, ambiguous, unmatched, without_link = 0, 0, 0, 0
    width = len(spec.columns)
    target_blocks, target_sequence = [], []
    for top in range(spec.target_start,destination.row_count+1,2000):
        bottom = min(destination.row_count,top+1999)
        values = writer.read(_a1(destination.title,spec.target_col,top,spec.target_col+width-1,bottom),render='FORMATTED_VALUE',what=f'核对目标 {top}-{bottom} 行')
        target_blocks.append((top,bottom,values))
        target_sequence.extend((top+offset,tuple(str(row[i]) if i<len(row) else '' for i in range(width))) for offset,row in enumerate(values))
    while target_sequence and not any(value.strip() for value in target_sequence[-1][1]):
        target_sequence.pop()
    same_sequence = len(source_sequence)==len(target_sequence) and all(
        row_number==spec.target_start+i and key==source_sequence[i][0]
        for i,(row_number,key) in enumerate(target_sequence)
    )
    if same_sequence:
        log('来源与目标整表内容及顺序完全一致，按原行位置恢复链接，可区分重名文件')
    stable_positions = same_sequence
    if not stable_positions and len(source_sequence)==len(target_sequence):
        source_positions, target_positions = {}, {}
        for i,(key,_links) in enumerate(source_sequence):source_positions.setdefault(key,[]).append(i)
        for i,(_number,key) in enumerate(target_sequence):target_positions.setdefault(key,[]).append(i)
        anchors = [(source_positions[key][0], positions[0]) for key,positions in target_positions.items()
                   if len(positions)==1 and len(source_positions.get(key,[]))==1]
        stable_positions = len(anchors)>=2 and all(left==right for left,right in anchors) and all(
            number==spec.target_start+i for i,(number,_key) in enumerate(target_sequence))
        if stable_positions:
            log(f'来源与目标行数及 {len(anchors)} 个唯一内容行的位置全部一致；只按原位置修复内容仍完全相同的行，已改动行跳过')
    for top,bottom,values in target_blocks:
        updates = {}
        for offset, row in enumerate(values):
            key = tuple(str(row[i]) if i<len(row) else '' for i in range(width))
            position = top+offset-spec.target_start
            positional_match = stable_positions and position<len(source_sequence) and key==source_sequence[position][0]
            candidates = ({relative:{uri} for relative,uri in source_sequence[position][1].items()}
                          if positional_match else source_index.get(key))
            if candidates is None:
                # Review/name/date columns may have changed since the merge. A
                # link caption that resolves to exactly one URL across all
                # sources still identifies that file without changing any data.
                candidates = {relative:uris for relative,_column in link_fields
                              if key[relative].strip() and len(uris:=label_index.get((relative,key[relative]),set()))==1}
                if not candidates:
                    if any(value.strip() for value in key): unmatched += 1
                    continue
            if not candidates:
                if any(value.strip() for value in key): without_link += 1
                continue
            for relative, uris in candidates.items():
                if len(uris) == 1:
                    updates.setdefault(relative,[]).append((top+offset,next(iter(uris))))
                else: ambiguous += 1
        requests = []
        for relative, rows in updates.items():
            groups = []
            for number, uri in rows:
                if groups and number == groups[-1][-1][0]+1:
                    groups[-1].append((number,uri))
                else:groups.append([(number,uri)])
            for group in groups:
                requests.append({'updateCells':{
                    'range':{'sheetId':destination.id,'startRowIndex':group[0][0]-1,'endRowIndex':group[-1][0],
                             'startColumnIndex':spec.target_col+relative,'endColumnIndex':spec.target_col+relative+1},
                    'rows':[{'values':[{'userEnteredFormat':{'textFormat':{'link':{'uri':uri}},'hyperlinkDisplayType':'LINKED'}}]} for _number,uri in group],
                    'fields':'userEnteredFormat.textFormat.link,userEnteredFormat.hyperlinkDisplayType',
                }})
                fixed += len(group)
        for start in range(0,len(requests),500):
            writer.execute(lambda body={'requests':requests[start:start+500]}:target.batch_update(body),what=f'修复目标 {top}-{bottom} 行超链接')
    log(f'超链接修复完成：{fixed} 个；来源无链接 {without_link} 行，不匹配 {unmatched} 行、冲突 {ambiguous} 个已跳过。未改动单元格文字、其他列或汇总数据')
    return {'mode':'worksheet_link_repair','total_rows':fixed,'unmatched':unmatched,'ambiguous':ambiguous,'without_source_link':without_link}
